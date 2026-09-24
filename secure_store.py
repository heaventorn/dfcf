# -*- coding: utf-8 -*-
"""DFCF 本地保险库：隐私文件加密 + 强口令校验。

它一次解决两件事：
  1) 口令难破解：密钥由「你输入的密码 + 本机密钥文件 data/pwd.key」经 Argon2id 派生，
     源码里不再留任何能离线爆破的哈希；密文用 AES-256-GCM 认证加密。
  2) 隐私文件加密：持仓 / 自选 / 策略 / cookies / API Key，以及 output/ 里的
     账本、日志、追踪等个人记录，落盘一律是 "文件名.enc" 密文。

密钥分两层，所以改密码不用重加密几百个文件：

    密码 + pwd.key  --Argon2id-->  KEK  --解开 keyring.json-->  DEK（数据密钥）
    DEK + 每个文件独立随机 nonce  --AES-256-GCM-->  文件密文

为什么这样设计（性能与安全）：
  * Argon2id 只在「解锁」时算一次，约 0.3~0.8 秒；之后每个文件都是纯 AES-GCM，
    几 KB 的隐私文件耗时在微秒级，日常使用感觉不到。
  * 每个文件独立 nonce，并把文件名绑进 AAD：文件被对调或篡改会直接解密失败。
  * 换密码只重新包一次 DEK（毫秒级），文件本身一个都不用动。
  * 密钥只放在内存和子进程环境变量里，不落盘；关掉程序就没了。

常用命令（也可以直接双击「加密工具.bat」看菜单）：
    python secure_store.py                     # 交互菜单
    python secure_store.py status              # 看加密状态
    python secure_store.py init                # 第一次：创建保险库
    python secure_store.py encrypt             # 把残留明文迁移成密文
    python secure_store.py rekey               # 换密码（不动密文本身）
    python secure_store.py edit strategy.json  # 用记事本改加密文件（存回自动加密）
    python secure_store.py backup D:\\备份\\vault.json
    python secure_store.py restore D:\\备份\\vault.json
"""

import base64
import datetime
import json
import os
import secrets
import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

import config

try:
    from argon2.low_level import Type as _Argon2Type
    from argon2.low_level import hash_secret_raw as _argon2_raw
    _ARGON2_OK = True
except Exception:                                    # pragma: no cover
    _Argon2_OK = False
    _ARGON2_OK = False

try:
    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    _AES_OK = True
except Exception:                                    # pragma: no cover
    class InvalidTag(Exception):
        pass
    _AES_OK = False


# ------------------------------------------------------------------ 常量

ENC_SUFFIX = ".enc"
ENV_KEY = "DFCF_VAULT_KEY"          # 只在进程之间传，不落盘
KEYRING_FILE = getattr(config, "VAULT_KEYRING_FILE",
                       os.path.join(config.DATA_DIR, "keyring.json"))
KEYFILE = config.PWD_KEY_FILE

# Argon2id 参数：64MB 内存 / 3 轮 / 4 线程（旧版是 16MB / 2 轮）。
# 单次 0.3~0.8 秒，只在启动解锁时算一次，之后读写文件不再碰它。
ARGON2_TIME = 3
ARGON2_MEM = 65536
ARGON2_PAR = 4
KEY_LEN = 32
SALT_LEN = 16
NONCE_LEN = 12
MIN_PWD = 6                          # 创建保险库时的硬门槛
WARN_PWD = 10                        # 短于这个长度只提示、不拦

AAD_DEK = b"dfcf-vault/keyring/dek/v1"
AAD_VERIFY = b"dfcf-vault/keyring/verify/v1"
AAD_FILE = b"dfcf-vault/file/v1:"
VERIFY_TEXT = b"dfcf-vault-ok"

# 老版本 auth_check.py 里那个内置哈希：只用于把老安装迁移过来；
# 迁移完成后源码里就再没有能离线爆破哈希的东西了。
LEGACY_ARGON2_HASH = ("$argon2id$v=19$m=16384,t=2,p=1$XRM95kjRmIq0w1TQ82qupw$"
                      "bKwFctLoRt6Ad1VxBCdiLS0HMqqqo8yEKFImUzJmVFQ")

_MISSING = object()
_DEK = None                          # 内存里的数据密钥
_SOURCE = ""                         # 密钥来源（状态里显示）


class VaultError(RuntimeError):
    """保险库可预期错误：密码错 / 缺密钥文件 / 依赖缺失 / 未解锁。"""


def deps_ok():
    """加密依赖是否齐全（argon2-cffi + cryptography）。"""
    return _ARGON2_OK and _AES_OK


def deps_hint():
    miss = []
    if not _ARGON2_OK:
        miss.append("argon2-cffi")
    if not _AES_OK:
        miss.append("cryptography")
    if not miss:
        return ""
    return "缺少依赖：%s  请执行  pip install %s" % (", ".join(miss), " ".join(miss))


# ------------------------------------------------------------------ 小工具

def _b64e(raw):
    return base64.b64encode(raw).decode("ascii")


def _b64d(text):
    return base64.b64decode(text.encode("ascii"))


def _norm(path):
    return os.path.normcase(os.path.abspath(path))


def enc_path(path):
    """明文路径 -> 密文路径（positions.json -> positions.json.enc）。"""
    return path + ENC_SUFFIX


def _file_tag(path):
    """绑进 AAD 的文件标识：相对项目根的路径，换盘符/换目录也不变。"""
    try:
        rel = os.path.relpath(os.path.abspath(path), BASE_DIR)
    except ValueError:
        rel = os.path.basename(path)
    return AAD_FILE + rel.replace("\\", "/").encode("utf-8", "surrogatepass")


def _now():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def is_protected(path):
    """这个文件是否属于「必须密文落盘」的隐私文件。"""
    p = _norm(path)
    if p == _norm(KEYRING_FILE) or p == _norm(KEYFILE):
        return False                                 # 这两个是钥匙，不是数据
    for x in getattr(config, "SECURE_FILES", ()) or ():
        if _norm(x) == p:
            return True
    try:
        rel = os.path.relpath(p, _norm(config.DATA_DIR))
    except ValueError:
        return False
    if rel.startswith(".."):
        return False
    # data/ 里所有 .json 都算隐私文件（以后新加的文件自动纳入）
    return rel.lower().endswith(".json")


def protected_paths():
    """需要加密的全部目标：显式清单 + data/ 下现存的 .json。"""
    out = []

    def _add(p):
        if _norm(p) not in [_norm(x) for x in out]:
            out.append(p)

    for p in list(getattr(config, "SECURE_FILES", ()) or ()):
        _add(p)
    for root, dirs, files in os.walk(config.DATA_DIR):
        dirs[:] = [d for d in dirs if d not in (".git", "__pycache__")]
        for name in files:
            p = os.path.join(root, name)
            if is_protected(p):
                _add(p)
    return out


def enc_files_present():
    """磁盘上有没有已经加密的 *.enc —— 用来守住「不能新建保险库」这条线。"""
    for d in (getattr(config, "DATA_DIR", None), getattr(config, "OUTPUT_DIR", None)):
        if not d or not os.path.isdir(d):
            continue
        for root, dirs, files in os.walk(d):
            dirs[:] = [x for x in dirs if x not in (".git", "__pycache__")]
            for name in files:
                if name.endswith(ENC_SUFFIX):
                    return True
    return False


# ------------------------------------------------------------------ 密钥文件

def load_keyfile(path=None):
    """读 data/pwd.key 的原始字节（缺失返回 None）。第二个因素的强度靠它。"""
    p = path or KEYFILE
    try:
        with open(p, "rb") as f:
            raw = f.read()
    except OSError:
        return None
    raw = raw.strip()
    return raw or None


def make_keyfile(path=None, overwrite=False):
    """生成 32 字节随机密钥文件（base64 文本），返回它的字节。"""
    p = path or KEYFILE
    if os.path.exists(p) and not overwrite:
        cur = load_keyfile(p)
        if cur:
            return cur
    blob = base64.urlsafe_b64encode(secrets.token_bytes(KEY_LEN))
    d = os.path.dirname(p)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(p, "wb") as f:
        f.write(blob + b"\n")
    try:
        os.chmod(p, 0o600)
    except OSError:
        pass
    return blob


def _derive(password, keyfile, salt, params):
    """Argon2id(密码 || 0x00 || 密钥文件) -> 32 字节 KEK。"""
    if not _ARGON2_OK:
        raise VaultError(deps_hint())
    secret = password.encode("utf-8") + b"\x00" + (keyfile or b"")
    return _argon2_raw(secret=secret, salt=salt,
                       time_cost=params["time"], memory_cost=params["mem"],
                       parallelism=params["par"], hash_len=params["len"],
                       type=_Argon2Type.ID)


def _params(kr=None):
    if isinstance(kr, dict) and isinstance(kr.get("params"), dict):
        p = kr["params"]
        return {"time": int(p.get("time", ARGON2_TIME)),
                "mem": int(p.get("mem", ARGON2_MEM)),
                "par": int(p.get("par", ARGON2_PAR)),
                "len": int(p.get("len", KEY_LEN))}
    return {"time": ARGON2_TIME, "mem": ARGON2_MEM, "par": ARGON2_PAR, "len": KEY_LEN}


# ------------------------------------------------------------------ 钥匙串

def has_vault():
    return os.path.exists(KEYRING_FILE)


def _keyring_load():
    try:
        with open(KEYRING_FILE, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return None
    except Exception as e:
        raise VaultError("%s 读不出来（%s），保险库文件可能坏了。" % (KEYRING_FILE, e))


def _keyring_save(kr):
    tmp = KEYRING_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(kr, f, ensure_ascii=False, indent=2)
    os.replace(tmp, KEYRING_FILE)
    try:
        os.chmod(KEYRING_FILE, 0o600)
    except OSError:
        pass


def create_vault(password, keyfile=None):
    """建保险库：随机 DEK，用 Argon2id(密码 + 密钥文件) 包住后存 keyring.json。"""
    global _DEK, _SOURCE
    if not deps_ok():
        raise VaultError(deps_hint())
    if has_vault():
        raise VaultError("已经有 %s 了，不能重复创建；想换密码请用 rekey。" % KEYRING_FILE)
    if enc_files_present():
        raise VaultError(
            "发现有已加密的文件（*.enc），但找不到 %s。\n"
            "这时候新建保险库会生成一把新钥匙，老文件就再也解不开了。\n"
            "请先恢复钥匙： python secure_store.py restore <你的备份文件>" % KEYRING_FILE)
    if not password:
        raise VaultError("密码不能为空。")
    keyfile = keyfile if keyfile is not None else make_keyfile()
    salt = secrets.token_bytes(SALT_LEN)
    params = _params()
    kek = _derive(password, keyfile, salt, params)
    dek = secrets.token_bytes(KEY_LEN)
    wrap_nonce = secrets.token_bytes(NONCE_LEN)
    wrapped = AESGCM(kek).encrypt(wrap_nonce, dek, AAD_DEK)
    v_nonce = secrets.token_bytes(NONCE_LEN)
    verifier = AESGCM(dek).encrypt(v_nonce, VERIFY_TEXT, AAD_VERIFY)
    _keyring_save({
        "v": 1, "alg": "AES-256-GCM", "kdf": "argon2id", "params": params,
        "salt": _b64e(salt), "keyfile": os.path.basename(KEYFILE),
        "wrap_nonce": _b64e(wrap_nonce), "wrapped_dek": _b64e(wrapped),
        "verify_nonce": _b64e(v_nonce), "verifier": _b64e(verifier),
        "created": _now(), "updated": _now(),
    })
    _DEK, _SOURCE = dek, "刚创建（内存）"
    return dek


def unlock_vault(password, keyfile=None, kr=None):
    """用密码解开 DEK；密码或密钥文件不对就抛 VaultError。"""
    global _DEK, _SOURCE
    if not deps_ok():
        raise VaultError(deps_hint())
    kr = kr if kr is not None else _keyring_load()
    if not kr:
        raise VaultError("还没有保险库（找不到 %s），请先运行 init。" % KEYRING_FILE)
    kf = keyfile if keyfile is not None else load_keyfile()
    if not kf:
        raise VaultError("找不到本机密钥文件：%s\n它是第二把钥匙，丢了就解不开，"
                         "请从备份里放回来。" % KEYFILE)
    salt = _b64d(kr["salt"])
    kek = _derive(password, kf, salt, _params(kr))
    try:
        dek = AESGCM(kek).decrypt(_b64d(kr["wrap_nonce"]), _b64d(kr["wrapped_dek"]),
                                  AAD_DEK)
    except InvalidTag:
        raise VaultError("密码错误（与本机密钥文件不匹配）。")
    try:
        AESGCM(dek).decrypt(_b64d(kr["verify_nonce"]), _b64d(kr["verifier"]),
                            AAD_VERIFY)
    except InvalidTag:
        raise VaultError("自检失败：keyring.json 可能被改过或损坏。")
    _DEK, _SOURCE = dek, "刚解锁（内存）"
    return dek


def rekey(new_password, new_keyfile=False):
    """换密码：只重新包一次 DEK，密文文件一个都不用动（毫秒级）。"""
    global _SOURCE
    dek = _require_key()
    kr = _keyring_load() or {}
    keyfile = load_keyfile()
    if new_keyfile or not keyfile:
        keyfile = make_keyfile(overwrite=True)
    salt = secrets.token_bytes(SALT_LEN)
    params = _params()
    kek = _derive(new_password, keyfile, salt, params)
    wrap_nonce = secrets.token_bytes(NONCE_LEN)
    wrapped = AESGCM(kek).encrypt(wrap_nonce, dek, AAD_DEK)
    kr.update({"v": 1, "alg": "AES-256-GCM", "kdf": "argon2id", "params": params,
               "salt": _b64e(salt), "keyfile": os.path.basename(KEYFILE),
               "wrap_nonce": _b64e(wrap_nonce), "wrapped_dek": _b64e(wrapped),
               "updated": _now()})
    _keyring_save(kr)
    _SOURCE = "换密码后（内存）"
    return dek


def lock():
    """把内存里的密钥扔掉（不退出程序也能手动上锁）。"""
    global _DEK, _SOURCE
    _DEK, _SOURCE = None, ""


def unlocked_key():
    """当前可用的 DEK：先看内存，再看子进程环境变量。"""
    global _DEK, _SOURCE
    if _DEK is not None:
        return _DEK
    env = os.environ.get(ENV_KEY)
    if env:
        try:
            raw = _b64d(env)
        except Exception:
            raw = b""
        if len(raw) == KEY_LEN:
            _DEK, _SOURCE = raw, "环境变量 %s（启动脚本传入）" % ENV_KEY
    return _DEK


def export_key(dek=None):
    """把 DEK 转成可放进环境变量的 base64（只在进程之间传）。"""
    return _b64e(dek if dek is not None else _require_key())


def key_source():
    unlocked_key()
    return _SOURCE or "未解锁"


def _require_key(prompt=True):
    k = unlocked_key()
    if k is not None:
        return k
    if not prompt:
        raise VaultError("保险库未解锁：本进程拿不到密钥。请用「启动爬虫.bat」"
                         "（或 python auth_check.py）启动。")
    k = unlock_interactive()
    if k is None:
        raise VaultError("保险库未解锁（用户取消或密码错误）。")
    return k


# ------------------------------------------------------------------ 文件加解密

def _encrypt_blob(dek, path, plain):
    nonce = secrets.token_bytes(NONCE_LEN)
    ct = AESGCM(dek).encrypt(nonce, plain, _file_tag(path))
    env = {"v": 1, "alg": "AES-256-GCM", "kdf": "argon2id",
           "name": os.path.basename(path), "at": _now(),
           "nonce": _b64e(nonce), "ct": _b64e(ct)}
    return json.dumps(env, ensure_ascii=False, indent=1).encode("utf-8")


def _looks_encrypted(raw):
    if not raw or not raw.lstrip().startswith(b"{"):
        return False
    head = raw[:400]
    return b'"ct"' in head and b'"nonce"' in head


def _decrypt_blob(dek, path, raw):
    try:
        env = json.loads(raw.decode("utf-8"))
        nonce = _b64d(env["nonce"])
        ct = _b64d(env["ct"])
    except Exception as e:
        raise VaultError("%s 不是合法的密文信封（%s）。" % (os.path.basename(path), e))
    try:
        return AESGCM(dek).decrypt(nonce, ct, _file_tag(path))
    except InvalidTag:
        raise VaultError("%s 解密失败：文件被改过、被换过，或者密钥不对。"
                         % os.path.basename(path))


def _read_plain(path):
    try:
        with open(path, "rb") as f:
            return f.read()
    except FileNotFoundError:
        return None
    except OSError:
        return None


def read_bytes(path, default=None, prompt=True):
    """读文件（受保护文件自动解密）。

    明文和密文同时存在时按修改时间取新的那份；读完顺手把明文迁移成密文，
    所以手工编辑过的明文不会留在磁盘上。
    """
    if not is_protected(path):
        raw = _read_plain(path)
        return default if raw is None else raw

    enc = enc_path(path)
    plain_raw = _read_plain(path)
    if os.path.exists(enc):
        enc_raw = _read_plain(enc)
        if plain_raw is not None:
            try:
                plain_newer = os.path.getmtime(path) > os.path.getmtime(enc)
            except OSError:
                plain_newer = False
            if plain_newer:
                try:                                     # 迁移失败也不影响读
                    write_bytes(path, plain_raw, prompt=prompt)
                except Exception:
                    pass
                return plain_raw
            try:                                         # 明文是旧的 -> 清掉
                os.remove(path)
            except OSError:
                pass
        return _decrypt_blob(_require_key(prompt), path, enc_raw)

    if plain_raw is not None:
        if _looks_encrypted(plain_raw):
            return _decrypt_blob(_require_key(prompt), path, plain_raw)
        try:                                             # 首次遇到明文 -> 立刻加密
            write_bytes(path, plain_raw, prompt=prompt)
        except Exception:
            pass
        return plain_raw
    return default


def write_bytes(path, data, prompt=True):
    """写文件（受保护文件自动加密，原子替换）。"""
    if not is_protected(path):
        d = os.path.dirname(path)
        if d:
            os.makedirs(d, exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "wb") as f:
            f.write(data)
        os.replace(tmp, path)
        return True

    blob = _encrypt_blob(_require_key(prompt), path, data)
    target = enc_path(path)
    d = os.path.dirname(target)
    if d:
        os.makedirs(d, exist_ok=True)
    tmp = target + ".tmp"
    with open(tmp, "wb") as f:
        f.write(blob)
    os.replace(tmp, target)
    if os.path.exists(path):                             # 明文立刻清掉
        try:
            _shred(path)
            os.remove(path)
        except OSError:
            pass
    return True


def read_text(path, default=None, prompt=True):
    raw = read_bytes(path, None, prompt=prompt)
    if raw is None:
        return default
    return raw.decode("utf-8", "replace")


def write_text(path, text, prompt=True):
    return write_bytes(path, (text or "").encode("utf-8"), prompt=prompt)


def read_json(path, default=_MISSING, prompt=True):
    """读 JSON（受保护文件自动解密）。不给 default 时，文件缺失就抛异常。"""
    raw = read_bytes(path, None, prompt=prompt)
    if raw is None:
        if default is _MISSING:
            raise FileNotFoundError(path)
        return default
    if not raw.strip():
        return default if default is not _MISSING else None
    return json.loads(raw.decode("utf-8"))


def write_json(path, obj, indent=2, prompt=True):
    text = json.dumps(obj, ensure_ascii=False, indent=indent)
    return write_text(path, text, prompt=prompt)


# ---- 透明的「像文件一样」的读写代理 -------------------------------------------
# 老代码里到处都是  with open(path) as f:  json.load(f) / json.dump(obj, f)
# 把 open(...) 换成这两个之一就够了，里面的读写一行都不用改，密文自动处理。


class _TextWriter(object):
    def __init__(self, path, prompt=True):
        self.name = path
        self._path = path
        self._prompt = prompt
        self._buf = []
        self._closed = False

    def write(self, s):
        if isinstance(s, bytes):
            s = s.decode("utf-8")
        self._buf.append(s)
        return len(s)

    def writelines(self, lines):
        for s in lines:
            self.write(s)

    def flush(self):
        pass

    def close(self):
        if self._closed:
            return
        self._closed = True
        write_bytes(self._path, "".join(self._buf).encode("utf-8"),
                    prompt=self._prompt)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        if exc_type is None:
            self.close()                 # 写不进去就照常往上抛，不吞异常
        return False


def open_writer(path, prompt=True):
    """with secure_store.open_writer(p) as f: json.dump(obj, f) —— 落盘自动加密。"""
    return _TextWriter(path, prompt=prompt)


def open_reader(path, prompt=True):
    """with secure_store.open_reader(p) as f: json.load(f) —— 读取自动解密。

    明文和密文都没有时抛 FileNotFoundError，跟内置 open 的行为一致。
    """
    if not exists(path):
        raise FileNotFoundError(path)
    import io
    return io.StringIO(read_text(path, "", prompt=prompt) or "")


def read_lines(path, prompt=True):
    """读 JSONL（每行一条）。文件不存在返回 []。"""
    raw = read_bytes(path, None, prompt=prompt)
    if not raw:
        return []
    return [ln for ln in raw.decode("utf-8", "replace").splitlines() if ln.strip()]


def write_lines(path, lines, prompt=True):
    return write_text(path, "\n".join(lines) + ("\n" if lines else ""), prompt=prompt)


def append_line(path, line, prompt=True):
    """给 JSONL 追加一行：读旧 -> 拼新 -> 整份加密写回。"""
    lines = read_lines(path, prompt=prompt)
    lines.append(line.rstrip("\n"))
    return write_lines(path, lines, prompt=prompt)


def exists(path):
    """明文或密文任一存在即为 True（调用方判空用）。"""
    return os.path.exists(path) or os.path.exists(enc_path(path))


def stamp(path):
    """「文件变没变」的指纹 (mtime, size)；密文/明文里取新的那份。"""
    cands = [p for p in (enc_path(path), path) if os.path.exists(p)]
    if not cands:
        return None
    best = max(cands, key=lambda p: os.path.getmtime(p))
    try:
        st = os.stat(best)
    except OSError:
        return None
    return (st.st_mtime_ns, st.st_size)


def _shred(path):
    """删明文前覆写一遍（机械盘能少留点残影；SSD 上只是个礼貌动作）。"""
    try:
        size = os.path.getsize(path)
    except OSError:
        return
    if size <= 0 or size > 32 * 1024 * 1024:
        return
    try:
        with open(path, "r+b") as f:
            f.write(secrets.token_bytes(size))
            f.flush()
            os.fsync(f.fileno())
    except OSError:
        pass


def encrypt_path(path, prompt=True):
    """把明文文件加密成 .enc（先验证能解回来，再删明文）。返回是否动过。"""
    raw = _read_plain(path)
    if raw is None:
        return False
    if _looks_encrypted(raw) and not os.path.exists(enc_path(path)):
        return False
    dek = _require_key(prompt)
    blob = _encrypt_blob(dek, path, raw)
    if _decrypt_blob(dek, path, blob) != raw:
        raise VaultError("%s 往返校验失败，已跳过（明文没删）。" % os.path.basename(path))
    target = enc_path(path)
    tmp = target + ".tmp"
    with open(tmp, "wb") as f:
        f.write(blob)
    os.replace(tmp, target)
    try:
        _shred(path)
        os.remove(path)
    except OSError:
        pass
    return True


def decrypt_to_text(path, prompt=True):
    raw = read_bytes(path, None, prompt=prompt)
    if raw is None:
        raise VaultError("找不到文件（明文和密文都没有）：%s" % path)
    return raw.decode("utf-8", "replace")


def export_plain(path, out_path, prompt=True):
    """导出某个隐私文件的明文（做备份用，注意放哪儿）。"""
    text = decrypt_to_text(path, prompt=prompt)
    d = os.path.dirname(os.path.abspath(out_path))
    if d:
        os.makedirs(d, exist_ok=True)
    with open(out_path, "w", encoding="utf-8", newline="") as f:
        f.write(text)
    return out_path


# ------------------------------------------------------------------ 批量迁移 / 状态

def migrate_all(quiet=False):
    """把所有残留的明文隐私文件加密掉，返回迁移数量。"""
    _require_key()
    n = 0
    for p in protected_paths():
        if not os.path.exists(p):
            continue
        try:
            if encrypt_path(p):
                n += 1
        except Exception as e:
            if not quiet:
                print("  [!] %s 加密失败：%s" % (os.path.basename(p), e))
    return n


def status_rows():
    """[(相对路径, 状态)]，给状态命令和菜单用。"""
    rows = []
    for p in protected_paths():
        name = os.path.relpath(p, BASE_DIR).replace("\\", "/")
        if os.path.exists(enc_path(p)) and os.path.exists(p):
            state = "密文 + 明文(待清理)"
        elif os.path.exists(enc_path(p)):
            state = "已加密"
        elif os.path.exists(p):
            state = "!! 明文未加密"
        else:
            state = "-"
        rows.append((name, state))
    return rows


def report():
    lines = []
    lines.append("项目目录 : %s" % BASE_DIR)
    lines.append("数据目录 : %s" % config.DATA_DIR)
    lines.append("加密依赖 : %s" % ("OK（argon2-cffi + cryptography）"
                                    if deps_ok() else deps_hint()))
    lines.append("保险库   : %s" % (KEYRING_FILE if has_vault()
                                    else "还没建（运行 init 创建）"))
    kf = load_keyfile()
    lines.append("密钥文件 : %s (%s)" % (KEYFILE,
                                     ("有，%d 字节" % len(kf)) if kf else "!! 缺失"))
    lines.append("当前密钥 : %s" % key_source())
    lines.append("")
    lines.append("隐私文件状态：")
    for name, state in status_rows():
        lines.append("  %-34s %s" % (name, state))
    lines.append("")
    lines.append("说明：output/index.html、latest_market.json、history.db 要给浏览器和"
                 "数据库直接读，不在加密范围；它们每次运行都会重建，内容里也含你的持仓，"
                 "所以别把整个项目目录同步到网盘或公开仓库。")
    return "\n".join(lines)


# ------------------------------------------------------------------ 密码输入界面

# 配色对齐隔壁 ACFUND-SHELL 的终端主题（见 acfund-shell/shell/theme.py）：
# 黑底、灰字、绿色高亮。以后想换颜色，只改这几个常量就行。
_TK_BG = "#0B0B0B"
_TK_SURFACE = "#141518"
_TK_BORDER = "#2A2D33"
_TK_TEXT = "#E6E6E6"
_TK_MUTED = "#6E747D"
_TK_ACCENT = "#3FB950"
_TK_ACCENT_HOVER = "#56D364"
_TK_DANGER = "#F85149"
_TK_MIN_WIDTH = 680          # 窗口最小宽度（像素），比原来的 400 宽不少


def _tk_pick_font(root, *names):
    """挑一个系统里真实存在的等宽字体；都没有就退回 Tk 默认等宽。"""
    try:
        import tkinter.font as tkfont
        avail = set(tkfont.families(root))
        for n in names:
            if n in avail:
                return n
    except Exception:
        pass
    return "Courier New"


def _prompt_tk(title, hint="", error="", confirm=False):
    """密码框（Tkinter）。尺寸和字号按隔壁 ACFUND-SHELL 的登录框比例放大。

    两条硬规矩，避免再出现"按钮太小、字显示不全"：
      1) 每个控件都显式给 font —— 不给就落到 Tk 默认字体，中文容易被裁；
      2) 按钮不写死 width，改成按文字自适应 + 足够的 padx/pady。
    窗口大小由内容撑开（取内容宽度和 _TK_MIN_WIDTH 里较大的那个），
    所以换台电脑、换个缩放比例也不会挤。
    """
    import tkinter as tk

    root = tk.Tk()
    root.title(title)
    root.resizable(False, False)
    root.configure(bg=_TK_BG)

    f_head = ("Microsoft YaHei", 15, "bold")
    f_ui = ("Microsoft YaHei", 12)
    f_small = ("Microsoft YaHei", 10)
    f_entry = ("Microsoft YaHei", 15)
    f_btn = ("Microsoft YaHei", 13, "bold")
    f_mono = (_tk_pick_font(root, "Cascadia Mono", "Consolas"), 11)

    out = {"v": None}
    done = {"s": False}       # 防重复触发（回车和按钮可能都调一次）

    pad = tk.Frame(root, bg=_TK_BG)
    pad.pack(fill="both", expand=True, padx=30, pady=26)

    tk.Label(pad, text=title, font=f_head, fg=_TK_TEXT, bg=_TK_BG,
             anchor="w").pack(fill="x")
    tk.Label(pad, text="─" * 56, font=f_mono, fg=_TK_BORDER, bg=_TK_BG,
             anchor="w").pack(fill="x", pady=(4, 12))
    if hint:
        tk.Label(pad, text=hint, fg=_TK_MUTED, bg=_TK_BG, font=f_small,
                 wraplength=_TK_MIN_WIDTH - 110, justify="left",
                 anchor="w").pack(fill="x", pady=(0, 14))

    def prompt_row(text):
        """一行提示 +（"> " 标记 + 输入框），和 ACFUND 的输入行同一套视觉。"""
        tk.Label(pad, text=text, font=f_ui, fg=_TK_TEXT, bg=_TK_BG,
                 anchor="w").pack(fill="x", pady=(0, 4))
        row = tk.Frame(pad, bg=_TK_BG)
        row.pack(fill="x", pady=(0, 14))
        tk.Label(row, text=">", font=("Consolas", 15, "bold"), fg=_TK_ACCENT,
                 bg=_TK_BG).pack(side="left", padx=(0, 8))
        ent = tk.Entry(row, show="*", font=f_entry, bg=_TK_SURFACE,
                       fg=_TK_TEXT, insertbackground=_TK_ACCENT,
                       relief="flat", justify="left", highlightthickness=1,
                       highlightbackground=_TK_BORDER,
                       highlightcolor=_TK_ACCENT)
        ent.pack(side="left", fill="x", expand=True, ipady=8)
        return ent

    e1 = prompt_row("访问密码")
    e1.focus_set()
    e2 = prompt_row("再输一次") if confirm else None

    status = tk.Label(pad, text=error, fg=_TK_DANGER, bg=_TK_BG,
                      font=f_small, wraplength=_TK_MIN_WIDTH - 110,
                      justify="left", anchor="w", height=2)
    status.pack(fill="x")

    def ok(event=None):
        if done["s"]:
            return
        pwd = e1.get()
        if not pwd:
            status.config(text="密码不能为空")
            e1.focus_set()
            return
        if confirm:
            if len(pwd) < MIN_PWD:
                status.config(text="密码太短，至少 %d 位" % MIN_PWD)
                e1.focus_set()
                return
            if pwd != e2.get():
                status.config(text="两次输入不一致，请重输一遍")
                e1.delete(0, "end")
                e2.delete(0, "end")
                e1.focus_set()
                return
        done["s"] = True
        out["v"] = pwd
        root.destroy()

    def cancel(event=None):
        if done["s"]:
            return
        done["s"] = True
        root.destroy()

    def mk_btn(parent, text, cmd, bg, fg, hover):
        """按钮：不写 width，靠 padx/pady 撑开，中文字一定放得下。"""
        b = tk.Button(parent, text=text, command=cmd, font=f_btn, bg=bg, fg=fg,
                      activebackground=hover, activeforeground=fg,
                      relief="flat", bd=0, highlightthickness=0,
                      cursor="hand2", padx=34, pady=12)
        b.bind("<Enter>", lambda ev: b.config(bg=hover))
        b.bind("<Leave>", lambda ev: b.config(bg=bg))
        return b

    bf = tk.Frame(pad, bg=_TK_BG)
    bf.pack(fill="x", pady=(16, 0))
    mk_btn(bf, "确定", ok, _TK_ACCENT, "#0B0B0B", _TK_ACCENT_HOVER).pack(
        side="left")
    mk_btn(bf, "取消", cancel, _TK_SURFACE, _TK_TEXT, "#1B1D21").pack(
        side="left", padx=(14, 0))
    tk.Label(bf, text="回车 = 确定　Esc = 取消", font=f_small, fg=_TK_MUTED,
             bg=_TK_BG).pack(side="right")

    root.bind("<Return>", ok)
    root.bind("<KP_Enter>", ok)
    root.bind("<Escape>", cancel)
    root.protocol("WM_DELETE_WINDOW", cancel)

    # 先让 Tk 算一遍内容要多宽多高，再按这个尺寸开窗（不够宽就用最小宽度）
    root.update_idletasks()
    w = max(root.winfo_reqwidth(), _TK_MIN_WIDTH)
    h = root.winfo_reqheight()
    root.geometry("%dx%d" % (w, h))
    root.minsize(w, h)
    try:
        root.eval("tk::PlaceWindow . center")
    except Exception:
        pass
    # 抢到键盘焦点：窗口要是开在别的窗口后面，敲字会掉进别人的怀里
    try:
        root.lift()
        root.focus_force()
        e1.focus_force()
        root.attributes("-topmost", True)
        root.after(800, lambda: root.attributes("-topmost", False))
    except Exception:
        pass
    root.mainloop()
    try:
        root.destroy()
    except Exception:
        pass
    return out["v"]


# ------------------------------------------------------------------ CMD 风格登录

_CONS_BRAND = "DFCF 本地终端"
_CANCELLED = object()       # 内部用：用户按了 Ctrl+C / 回车留空，放弃本次输入


class _NoMaskedInput(Exception):
    """这个环境没法自己读按键（不是真控制台），交给上层走 getpass。"""


class _Stuck(Exception):
    """提示已经打出来了、键盘还是一点反应都没有，上层换弹窗接着问。"""


_CONS_HINT_AFTER = 10.0     # 一直没按键，多久之后给一句提示
_CONS_STUCK_AFTER = 25.0    # 再多久还这样，就判定"打不进字"，换弹窗


# 中文输入法开着的时候，控制台里敲字母会被它吃进候选框，程序一个字符都收不到
# —— 这就是"密码打不进去"的头号原因。所以进密码框之前自动切到英文键盘，
# 问完再把你原来的输入法还回去（不影响你在别处打中文）。

_WM_INPUTLANGCHANGEREQUEST = 0x0050


def _user32():
    """拿 user32 并声明函数签名（64 位下不声明，句柄会被截断）。"""
    import ctypes
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.GetForegroundWindow.restype = ctypes.c_void_p
    user32.GetWindowThreadProcessId.argtypes = (ctypes.c_void_p, ctypes.c_void_p)
    user32.GetWindowThreadProcessId.restype = ctypes.c_uint32
    user32.GetKeyboardLayout.argtypes = (ctypes.c_uint32,)
    user32.GetKeyboardLayout.restype = ctypes.c_void_p
    user32.GetKeyboardLayoutList.argtypes = (ctypes.c_int,
                                             ctypes.POINTER(ctypes.c_void_p))
    user32.GetKeyboardLayoutList.restype = ctypes.c_int
    user32.PostMessageW.argtypes = (ctypes.c_void_p, ctypes.c_uint,
                                    ctypes.c_void_p, ctypes.c_void_p)
    user32.PostMessageW.restype = ctypes.c_int
    user32.ActivateKeyboardLayout.argtypes = (ctypes.c_void_p, ctypes.c_uint)
    user32.ActivateKeyboardLayout.restype = ctypes.c_void_p
    return user32


def _english_layout():
    """系统里那个英文键盘（优先"英语（美国）"）；没装西文键盘就返回 None。"""
    try:
        import ctypes
        user32 = _user32()
        n = user32.GetKeyboardLayoutList(0, None)
        if n <= 0:
            return None
        buf = (ctypes.c_void_p * n)()
        n = user32.GetKeyboardLayoutList(n, buf)
        fallback = None
        for i in range(n):
            hkl = buf[i]
            if not hkl:
                continue
            lang = hkl & 0xFFFF
            if lang == 0x0409:                    # 英语（美国）—— 就用它
                return hkl
            # 中文/日文/韩文这几个就算了吧，换了等于没换
            if lang not in (0x0804, 0x0404, 0x0C04, 0x1004, 0x0411, 0x0412):
                if fallback is None:
                    fallback = hkl
        return fallback
    except Exception:
        return None


def _switch_layout(hkl):
    """把前台窗口和本线程都切到 hkl；返回切换前的 HKL（切不了返回 None）。"""
    try:
        import ctypes
        user32 = _user32()
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return None
        tid = user32.GetWindowThreadProcessId(ctypes.c_void_p(hwnd), None)
        old = user32.GetKeyboardLayout(tid) or None
        # 跨进程切别人的窗口，标准做法就是发这条消息（同步改自己的那份用下面这行）
        user32.PostMessageW(ctypes.c_void_p(hwnd), _WM_INPUTLANGCHANGEREQUEST,
                            None, ctypes.c_void_p(hkl))
        user32.ActivateKeyboardLayout(ctypes.c_void_p(hkl), 0)
        return old
    except Exception:
        return None


def _use_english_layout():
    """问密码前把输入法切成英文，返回"切换前的 HKL"（给还原用）。"""
    try:
        import time
        eng = _english_layout()
        if not eng:
            return None
        old = _switch_layout(eng)
        if not old or old == eng:
            return None
        time.sleep(0.15)          # 那条消息是异步的，给它一点时间落地
        return old
    except Exception:
        return None


def _restore_layout(old):
    """把输入法还给用户（原来什么就还什么）。"""
    if old:
        _switch_layout(old)


def _has_console():
    """当前进程是不是挂着一个能打字的控制台窗口。

    双击 加密工具.bat / 启动爬虫.bat 就是这种：黑窗口就在屏幕上，
    密码直接在里面敲最省事，不用再弹一个框。
    从别的图形程序用管道拉起来时 stdin 不是控制台，这里返回 False，
    那就退回弹窗（所以两种用法都不会瞎掉）。
    """
    try:
        if sys.stdin is None or sys.stdout is None:
            return False
        return bool(sys.stdin.isatty() and sys.stdout.isatty())
    except Exception:
        return False


def _console_head(title, hint, error, confirm, retry=False):
    """登录头只留一行：「DFCF 本地终端 · 身份验证」。

    没有边框、没有说明段 —— 重试时只补一行出错原因，别的不重复。
    """
    if retry:
        if error:
            print("  [!] %s" % error)
        else:
            print("  再试一次：")
        return
    print("")
    print("  %s" % (_CONS_BRAND + (" · %s" % title if title else "")))
    print("")
    if error:
        print("  [!] %s" % error)


def _read_hidden(prompt, hint_after=None, stuck_after=None):
    """自己读一行不回显的密码，每敲一个键回一个 *。

    不用系统的 getpass，是因为它一个字符都不回显 —— 敲了半天屏幕没动静，
    谁都会以为"键盘打不进去"。这里自己做三件事：
      1. 敲一下冒一个 *，看得见才算数（退格、粘贴、Ctrl+C 都能用）；
      2. 开着窗口时先把"快速编辑"关掉 —— 那模式一选中窗口就整个卡住不收
         键，很多人"打不进字"其实是被它卡的，读完再原样还原；
      3. 一直没收到任何按键就先提示一句，到 stuck_after 还没动静就抛
         _Stuck，让上层换弹窗接着问，不把人堵在门外。
    """
    import ctypes
    import msvcrt
    import time

    if hint_after is None:
        hint_after = _CONS_HINT_AFTER
    if stuck_after is None:
        stuck_after = _CONS_STUCK_AFTER

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.GetStdHandle.restype = ctypes.c_void_p
    kernel32.GetStdHandle.argtypes = (ctypes.c_uint32,)
    handle = kernel32.GetStdHandle(ctypes.c_uint32(0xFFFFFFF6))     # -10
    if not handle or handle == ctypes.c_void_p(-1).value:
        raise _NoMaskedInput("拿不到标准输入句柄")
    kernel32.GetConsoleMode.argtypes = (ctypes.c_void_p,
                                        ctypes.POINTER(ctypes.c_uint32))
    kernel32.SetConsoleMode.argtypes = (ctypes.c_void_p, ctypes.c_uint32)
    mode = ctypes.c_uint32()
    if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
        raise _NoMaskedInput("标准输入不是控制台")
    old_mode = mode.value
    # ENABLE_EXTENDED_FLAGS 打开后才有资格改 ENABLE_QUICK_EDIT_MODE 这一位
    kernel32.SetConsoleMode(handle, (old_mode | 0x0080) & ~0x0040)

    out = sys.stdout
    buf = []
    keys = 0
    hinted = False
    started = last = time.time()
    try:
        out.write(prompt)
        out.flush()
        while True:
            if msvcrt.kbhit():
                c = msvcrt.getwch()
                keys += 1
                last = time.time()
                if c in ("\r", "\n"):
                    break
                if c == "\003":                     # Ctrl+C
                    raise KeyboardInterrupt
                if c == "\b":                       # 退格
                    if buf:
                        buf.pop()
                        out.write("\b \b")
                        out.flush()
                    continue
                if c in ("\x00", "\xe0"):           # 功能键：后面还跟一个字节
                    msvcrt.getwch()
                    continue
                if c in ("\t", "\x1b"):             # Tab / 方向键序列，忽略
                    continue
                buf.append(c)
                out.write("*")
                out.flush()
                continue
            time.sleep(0.03)
            idle = time.time() - last
            if keys == 0 and not hinted and idle >= hint_after:
                hinted = True
                out.write("\n  [i] 按键没反应？先用鼠标在窗口里点一下再敲。\n")
                out.write(prompt + "*" * len(buf))
                out.flush()
            elif keys == 0 and time.time() - started >= stuck_after:
                raise _Stuck
    finally:
        try:
            kernel32.SetConsoleMode(handle, old_mode)
        except Exception:
            pass
        out.write("\n")
        out.flush()
    return "".join(buf)


def _ask_hidden(tip):
    """问一行密码（不回显，每敲一个键回一个 *）。取消返回 _CANCELLED。"""
    prompt = "  %s > " % tip
    try:
        return _read_hidden(prompt)
    except _Stuck:
        raise
    except KeyboardInterrupt:
        print("  [i] 已取消。")
        return _CANCELLED
    except Exception:
        pass
    # 兜底一：系统自带的 getpass（虽然不回显，但至少是标准做法）
    import getpass
    try:
        return getpass.getpass(prompt)
    except (EOFError, KeyboardInterrupt):
        print("  [i] 已取消。")
        return _CANCELLED
    except Exception:
        pass
    # 兜底二：实在不行就明着敲 —— 宁可看着不体面，也不能把人挡在门外
    try:
        return input(prompt)
    except (EOFError, KeyboardInterrupt):
        print("  [i] 已取消。")
        return _CANCELLED


def _prompt_console(title="身份验证", hint="", error="", confirm=False):
    """就在 CMD 窗口里问密码。返回 None 表示取消。"""
    _console_head(title, hint, error, confirm, retry=bool(error))
    try:
        # 设密码（输两遍）时允许在原地重来；验密码时只问一次，错了由外层再问
        for _ in range(3 if confirm else 1):
            pwd = _ask_hidden("访问密码")
            if pwd is _CANCELLED:
                return None
            if not pwd:
                print("  [!] 密码不能为空。")
                continue
            if confirm:
                if len(pwd) < MIN_PWD:
                    print("  [!] 密码太短，至少 %d 位。" % MIN_PWD)
                    continue
                again = _ask_hidden("再输一次")
                if again is _CANCELLED:
                    return None
                if pwd != again:
                    print("  [!] 两次输入不一致，重来一遍。")
                    continue
            return pwd
    except _Stuck:
        # 键盘在这个窗口里一直没反应（快速编辑卡住 / 输入法吞键之类）：
        # 换弹窗把同一个问题问完，别让人干瞪眼看着光标。
        print("  [i] 改用弹窗输入。")
        try:
            return _prompt_tk(title, hint=hint, error=error, confirm=confirm)
        except Exception:
            return None
    return None


def prompt_password(title="身份验证", hint="", error="", confirm=False):
    """要密码。返回 None 表示取消。

    默认就在当前 CMD 窗口里输入 —— 双击 加密工具.bat / 启动爬虫.bat 时，
    桌面上的黑窗口就是登录界面，跟隔壁 ACFUND-SHELL 那种终端问答一个味道。
    只有拿不到控制台（被别的图形程序用管道拉起）时才弹密码框兜底。

    问之前会先把输入法切到英文键盘（中文输入法会把字母吃进候选框，密码就
    一个字符都进不来），问完自动切回你原来那个。
    """
    old_layout = _use_english_layout()
    try:
        return _ask_password(title, hint, error, confirm)
    finally:
        _restore_layout(old_layout)


def _ask_password(title, hint, error, confirm):
    if _has_console():
        try:
            return _prompt_console(title=title, hint=hint, error=error,
                                   confirm=confirm)
        except Exception:
            # CMD 这条路出了意料之外的岔子：退回弹窗，别把人挡在门外
            try:
                return _prompt_tk(title, hint=hint, error=error,
                                  confirm=confirm)
            except Exception:
                return None
    try:
        return _prompt_tk(title, hint=hint, error=error, confirm=confirm)
    except Exception:
        return _prompt_console(title=title, hint=hint, error=error,
                               confirm=confirm)


def prompt_yes_no(question, default=False):
    tail = " [Y/n] " if default else " [y/N] "
    try:
        ans = input(question + tail).strip().lower()
    except (EOFError, KeyboardInterrupt):
        return False
    if not ans:
        return default
    return ans in ("y", "yes", "是", "1")


def _legacy_ok(password):
    """老安装的密码校验：Argon2(密码 + pwd.key 内容) 对着老哈希验。

    返回 True=通过 / False=不通过 / None=没法验（缺密钥文件或缺 argon2）。
    """
    if not _ARGON2_OK:
        return None
    kf = load_keyfile()
    if not kf:
        return None
    try:
        from argon2 import PasswordHasher
        PasswordHasher().verify(LEGACY_ARGON2_HASH,
                               password + kf.decode("utf-8", "replace"))
        return True
    except Exception:
        return False


def _bootstrap_after_create():
    """建库之后：把现存明文隐私文件全部加密，并报告数量。"""
    n = migrate_all(quiet=True)
    print("[OK] 保险库已创建，%d 个已有明文文件已加密为 *.enc。" % n)
    print("     提醒：data/pwd.key 是第二把钥匙，丢了解不开 —— 建议运行一次 "
          "python secure_store.py backup 备份到别处。")


def unlock_interactive(allow_create=True, max_tries=3):
    """要密码并解锁；返回 DEK，取消或失败返回 None。

    还没有 keyring.json（第一次运行）时顺手建库，并把现存明文加密。
    老安装会先用旧哈希验一次密码，验过才接手，不会把你的密码改掉。
    """
    if unlocked_key() is not None:
        return _DEK                          # 已经解锁过（同一进程里只问一次）
    if not deps_ok():
        print(deps_hint())
        return None

    if has_vault():
        err = ""
        for _ in range(max_tries):
            pwd = prompt_password(title="身份验证", error=err,
                                  hint="密码 + 本机密钥文件 data/pwd.key")
            if pwd is None:
                return None
            try:
                return unlock_vault(pwd)
            except VaultError as e:
                err = str(e)
        print("[!] 连续输错，已退出。")
        return None

    if not allow_create:
        return None

    keyfile = load_keyfile()
    if keyfile is None:
        # 全新机器：这台机器上还没有密钥文件，也没有密文数据 -> 直接建库
        hint = ("第一次在这台机器上使用：请设置访问密码（至少 %d 位）。\n"
                "会同时在 data/ 生成 pwd.key 密钥文件，两样都要留好。" % MIN_PWD)
        pwd = prompt_password(title="创建本地保险库", hint=hint, confirm=True)
        if pwd is None:
            return None
        if len(pwd) < WARN_PWD:
            print("[提示] 密码偏短，建议 12 位以上或一句好记的长口令。")
        try:
            dek = create_vault(pwd, None)
        except VaultError as e:
            print("[!] %s" % e)
            return None
        _bootstrap_after_create()
        return dek

    # 老安装：有 pwd.key 但没有保险库 -> 先按老规则验密码
    err = ""
    last = ""
    for _ in range(max_tries):
        pwd = prompt_password(title="升级本地保险库", error=err,
                              hint="请输入你原来用的访问密码；输对后自动升级成加密保险库。")
        if pwd is None:
            return None
        last = pwd
        if _legacy_ok(pwd):
            try:
                dek = create_vault(pwd, keyfile)
            except VaultError as e:
                print("[!] %s" % e)
                return None
            _bootstrap_after_create()
            return dek
        err = "密码错误（与本机密钥文件不匹配）。"
    # 老哈希被改过（比如自己轮换过）时给一条出路：没有密文数据，就不算降级
    print("[!] 老密码校验没通过。")
    if prompt_yes_no("改用刚才输入的密码新建保险库吗？（之后就用这个新密码解锁）"):
        try:
            dek = create_vault(last, keyfile)
        except VaultError as e:
            print("[!] %s" % e)
            return None
        _bootstrap_after_create()
        return dek
    return None


# ------------------------------------------------------------------ 备份 / 恢复

def backup(out_path):
    """把 keyring + pwd.key 打包成一个文件（等于备用钥匙，放到离线位置存好）。"""
    kr = _keyring_load()
    if not kr:
        raise VaultError("还没建保险库，没什么可备份。")
    kf = load_keyfile()
    if not kf:
        raise VaultError("找不到密钥文件 %s。" % KEYFILE)
    pack = {"v": 1, "at": _now(),
            "note": "DFCF 保险库备份：含 keyring 与密钥文件，等于备用钥匙，"
                    "请放到离线位置（U 盘 / 密码管理器）保存。",
            "keyring": kr, "keyfile": _b64e(kf)}
    out_abs = os.path.abspath(out_path)
    d = os.path.dirname(out_abs)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(out_abs, "w", encoding="utf-8") as f:
        json.dump(pack, f, ensure_ascii=False, indent=2)
    try:
        os.chmod(out_abs, 0o600)
    except OSError:
        pass
    return out_abs


def restore(in_path):
    """从备份文件恢复 keyring + pwd.key（换机器、丢文件时用）。"""
    with open(in_path, encoding="utf-8") as f:
        pack = json.load(f)
    kr = pack.get("keyring")
    kf = pack.get("keyfile")
    if not isinstance(kr, dict) or not kf:
        raise VaultError("备份文件内容不对：%s" % in_path)
    _keyring_save(kr)
    with open(KEYFILE, "wb") as f:
        f.write(_b64d(kf) + b"\n")
    try:
        os.chmod(KEYFILE, 0o600)
    except OSError:
        pass
    return KEYRING_FILE, KEYFILE


# ------------------------------------------------------------------ 命令行

def _cli_status():
    print(report())
    return 0


def _cli_init():
    if has_vault():
        print("已经有了：%s" % KEYRING_FILE)
        print("想换密码请用 rekey。")
        return 0
    if not deps_ok():
        print(deps_hint())
        return 1
    keyfile = load_keyfile()
    if keyfile is not None:
        print("检测到已有 data/pwd.key（老安装），改用 auth_check.py 启动即可自动升级；"
              "这里也可以直接设置新密码。")
    hint = "请设置访问密码（至少 %d 位）。会同时生成 data/pwd.key。" % MIN_PWD
    pwd = prompt_password(title="创建本地保险库", hint=hint, confirm=True)
    if pwd is None:
        print("已取消。")
        return 1
    try:
        create_vault(pwd, keyfile)
    except VaultError as e:
        print("[!] %s" % e)
        return 1
    _bootstrap_after_create()
    return 0


def _cli_encrypt():
    n = migrate_all()
    print("已加密 %d 个文件。" % n)
    return 0


def _cli_rekey():
    key = unlock_interactive(allow_create=False)
    if key is None:
        print("需要先解锁（或先 init）。")
        return 1
    pwd = prompt_password(title="设置新密码", confirm=True,
                          hint="换密码只重新包一次数据密钥，密文文件不用重加密。")
    if pwd is None:
        print("已取消。")
        return 1
    new_kf = prompt_yes_no("顺便重新生成 data/pwd.key 吗？（换机器时建议是）",
                           default=False)
    rekey(pwd, new_keyfile=new_kf)
    print("[OK] 密码已更新（密文文件没有改动）。")
    if new_kf:
        print("[OK] data/pwd.key 也已重新生成，记得重新备份一次。")
    return 0


def _resolve_named(name):
    if not name:
        print("[!] 要写文件名，比如 strategy.json")
        return None
    for p in protected_paths():
        if os.path.basename(p).lower() == os.path.basename(name).lower():
            return p
    print("[!] 名单里没有这个文件：%s" % name)
    return None


def _cli_edit(name):
    path = _resolve_named(name)
    if path is None:
        return 1
    import subprocess
    import tempfile
    try:
        text = decrypt_to_text(path)
    except VaultError as e:
        print("[!] %s" % e)
        return 1
    tmpdir = tempfile.mkdtemp(prefix="dfcf-edit-")
    tmp = os.path.join(tmpdir, os.path.basename(path))
    with open(tmp, "w", encoding="utf-8", newline="") as f:
        f.write(text)
    print("[..] 用记事本改这个临时文件：\n     %s\n     保存并关闭窗口后会自动加密回 "
          "%s" % (tmp, enc_path(path)))
    try:
        subprocess.Popen(["notepad.exe", tmp]).wait()
    except Exception as e:
        print("[!] 打不开记事本（%s）。你可以手动改这个文件，然后运行 python "
              "secure_store.py encrypt：" % e)
        print("     " + tmp)
        return 1
    with open(tmp, encoding="utf-8") as f:
        new_text = f.read()
    if new_text == text:
        print("[OK] 内容没变，什么都不做。")
    else:
        if path.lower().endswith(".json"):
            try:
                json.loads(new_text)
            except Exception as e:
                print("[!] 保存的内容不是合法 JSON（%s），为免弄坏数据没有写入。" % e)
                print("     明文还留在：%s" % tmp)
                return 1
        write_text(path, new_text)
        print("[OK] 已加密写回 %s" % enc_path(path))
    try:
        os.remove(tmp)
        os.rmdir(tmpdir)
    except OSError:
        pass
    return 0


def _cli_backup(path):
    print("[OK] 已写出备份：%s" % backup(path))
    print("     它等于一把备用钥匙，请放到离线位置（U 盘 / 密码管理器）保存。")
    return 0


def _cli_restore(path):
    kr, kf = restore(path)
    print("[OK] 已恢复：\n     %s\n     %s" % (kr, kf))
    return 0


def _cli_export(name, out):
    path = _resolve_named(name)
    if path is None:
        return 1
    export_plain(path, out)
    print("[OK] 明文已导出：%s" % out)
    print("     注意：它就是明文，看完记得删掉。")
    return 0


def menu():
    """给不熟命令行的人：一个数字菜单。"""
    while True:
        print()
        print("=" * 56)
        print("  DFCF 加密保险库 · 小工具")
        print("=" * 56)
        print("  1) 查看加密状态")
        print("  2) 创建保险库（第一次用）")
        print("  3) 把残留明文加密（升级或手工加了文件后）")
        print("  4) 修改访问密码")
        print("  5) 导出备份（强烈建议做一次）")
        print("  6) 从备份恢复（换机器 / 丢了 pwd.key）")
        print("  7) 编辑某个加密文件（记事本改，存回自动加密）")
        print("  8) 导出某个文件的明文（临时看内容）")
        print("  0) 退出")
        try:
            pick = input("请选择 > ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if pick == "0":
            return 0
        if pick == "1":
            _cli_status()
        elif pick == "2":
            _cli_init()
        elif pick == "3":
            _cli_encrypt()
        elif pick == "4":
            _cli_rekey()
        elif pick == "5":
            out = input("备份文件写到哪里？（例如 D:\\备份\\dfcf-vault.json）> ").strip()
            if out:
                _cli_backup(out)
        elif pick == "6":
            src = input("备份文件路径 > ").strip()
            if src:
                _cli_restore(src)
        elif pick == "7":
            name = input("要改哪个文件？（例如 strategy.json）> ").strip()
            _cli_edit(name)
        elif pick == "8":
            name = input("要导出哪个文件？> ").strip()
            out = input("导出的明文写到哪里？> ").strip()
            if name and out:
                _cli_export(name, out)
        else:
            print("[!] 没这个选项。")


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        return menu()
    cmd = argv[0].lower()
    arg = argv[1] if len(argv) > 1 else ""
    if cmd in ("status", "状态"):
        return _cli_status()
    if cmd in ("init", "创建"):
        return _cli_init()
    if cmd in ("encrypt", "加密"):
        return _cli_encrypt()
    if cmd in ("rekey", "改密码"):
        return _cli_rekey()
    if cmd == "edit":
        return _cli_edit(arg)
    if cmd == "backup":
        return _cli_backup(arg or "dfcf-vault-backup.json")
    if cmd == "restore":
        return _cli_restore(arg)
    if cmd == "export":
        return _cli_export(arg, argv[2] if len(argv) > 2 else (arg + ".plain"))
    if cmd in ("lock", "上锁"):
        lock()
        print("[OK] 内存里的密钥已清掉。")
        return 0
    print(__doc__)
    return 1


if __name__ == "__main__":
    sys.exit(main())
