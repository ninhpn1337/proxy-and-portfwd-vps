#!/usr/bin/env python3
import os
import re
import sys
import time
import shutil
import platform
import subprocess

try:
    # Bật lịch sử dòng nhập cho input() -> dùng mũi tên lên/xuống để lấy lại
    # dòng vừa gõ (kể cả dòng bị từ chối do sai) và sửa thay vì gõ lại từ đầu.
    import readline  # noqa: F401
except ImportError:
    pass  # Windows không có readline sẵn -> bỏ qua, không ảnh hưởng chức năng chính.

FRP_VERSION = "0.58.1"

# uname -m / platform.machine() -> tên arch dùng trong tên file release của frp
ARCH_MAP = {
    "x86_64": "amd64",
    "amd64": "amd64",
    "aarch64": "arm64",
    "arm64": "arm64",
    "armv7l": "arm",
    "armv6l": "arm",
    "i386": "386",
    "i686": "386",
}

# Giá trị mặc định cho VPS hiện tại, chỉ để đỡ phải gõ lại IP/token mỗi lần.
# Port NAT (external <-> internal) KHÔNG đưa vào đây vì nhà cung cấp cấp port
# ngẫu nhiên mỗi lần -> phải kiểm tra trong panel VPS và nhập tay mỗi lần.
DEFAULT_VPS = {
    "ip": "",
    "control_public_port": "",  # port public NAT trỏ vào bindPort của frps
    "token": "Kubark",
}


def ensure_paramiko():
    try:
        import paramiko
    except ImportError:
        print("[*] Đang cài đặt thư viện paramiko...")
        base_cmd = [sys.executable, "-m", "pip", "install", "--user", "paramiko", "-q"]
        try:
            subprocess.check_call(base_cmd)
        except subprocess.CalledProcessError:
            # Debian/Ubuntu mới (PEP 668) chặn pip ngoài venv -> thử cờ break-system-packages
            subprocess.check_call(base_cmd + ["--break-system-packages"])
        import paramiko
    return paramiko


def default_suffix(value):
    """In '(mặc định ...)' chỉ khi value có giá trị thật; value rỗng ("" / None)
    thì bỏ qua để tránh prompt_int/prompt_str cố dùng default rỗng -> crash."""
    return f" (mặc định {value})" if value else ""


def prompt_int(message, default=None):
    while True:
        raw = input(message).strip()
        if not raw:
            if default is not None:
                return int(default)
            print("[-] Giá trị không được để trống, nhập lại.")
            continue
        try:
            return int(raw)
        except ValueError:
            print("[-] Vui lòng nhập một số nguyên hợp lệ.")


def prompt_str(message):
    while True:
        raw = input(message).strip()
        if raw:
            return raw
        print("[-] Giá trị không được để trống, nhập lại.")


def prompt_port_pair(message):
    """Bắt nhập ĐÚNG 1 DÒNG từ panel VPS, dạng 'PUBLIC -> INTERNAL' (chấp nhận
    '->' , ':' , ',' hoặc khoảng trắng làm dấu ngăn cách), để tránh việc người
    dùng tự ghép 2 số lấy từ 2 dòng khác nhau trong bảng NAT."""
    pattern = re.compile(r"^\s*(\d+)\D+(\d+)\s*$")
    while True:
        raw = input(message).strip()
        m = pattern.match(raw)
        if not m:
            print("[-] Không đọc được. Gõ ĐÚNG 1 DÒNG trong panel VPS, dạng PUBLIC->INTERNAL, ví dụ: 3379->8080")
            continue
        public_port, internal_port = int(m.group(1)), int(m.group(2))
        print(f"    => Hiểu là: PUBLIC (cổng ngoài, người khác gõ vào) = {public_port}")
        print(f"       INTERNAL (cổng nội bộ, giá trị thật của remotePort) = {internal_port}")
        confirm = input("    Đúng với dòng trong panel VPS chưa? (Enter = đúng / n = nhập lại): ").strip().lower()
        if confirm in ("", "y", "yes"):
            return public_port, internal_port
        print("[*] Nhập lại cặp port.")


def prompt_service_ports():
    print()
    print("[*] Cần 2 thông tin KHÁC NHAU, đừng lẫn:")
    print("    1) Local Port  -> port TRÊN CHÍNH MÁY NÀY (Kali), nơi dịch vụ thật đang chạy.")
    print("    2) Cặp NAT port -> COPY NGUYÊN 1 DÒNG trong panel quản lý VPS (bảng Forwarded")
    print("       Ports), KHÔNG tự ghép 2 số từ 2 dòng khác nhau.")
    print()

    local_port = prompt_int("Nhập Local Port (port đang mở TRÊN MÁY NÀY, ví dụ 27015): ")
    external_port, internal_port = prompt_port_pair(
        "\nNhập cặp NAT port từ panel VPS (dạng PUBLIC->INTERNAL, ví dụ 3379->8080): "
    )

    print("\n[*] Tóm tắt cấu hình dịch vụ:")
    print(f"    - Local Port (trên máy này):      {local_port}")
    print(f"    - Public Port (người khác gõ vào): {external_port}")
    print(f"    - Internal Port (remotePort thật):  {internal_port}")

    if internal_port == external_port:
        print(
            "[!] Cảnh báo: Public và Internal đang BẰNG NHAU — với NAT VPS, 2 giá trị này "
            "thường KHÁC NHAU. Kiểm tra lại panel VPS nếu không chắc đây là NAT port thật."
        )

    return local_port, external_port, internal_port


def local_arch():
    m = platform.machine().lower()
    arch = ARCH_MAP.get(m)
    if not arch:
        print(f"[-] Không nhận diện được kiến trúc CPU máy local: {m}")
        sys.exit(1)
    return arch


def run_ssh(ssh, cmd, check=False):
    stdin, stdout, stderr = ssh.exec_command(cmd)
    exit_code = stdout.channel.recv_exit_status()
    out = stdout.read().decode(errors="ignore")
    err = stderr.read().decode(errors="ignore")
    if check and exit_code != 0:
        raise RuntimeError(f"Lệnh thất bại ({exit_code}): {cmd}\n{err}")
    return exit_code, out, err


def remote_arch(ssh):
    _, out, _ = run_ssh(ssh, "uname -m")
    m = out.strip().lower()
    arch = ARCH_MAP.get(m)
    if not arch:
        print(f"[-] VPS dùng kiến trúc CPU không được hỗ trợ: {m}")
        sys.exit(1)
    return arch


def remote_frp_version(ssh, binary_path):
    exit_code, out, _ = run_ssh(ssh, f"test -x {binary_path} && {binary_path} --version 2>/dev/null")
    if exit_code != 0:
        return None
    return out.strip() or None


def local_frp_version(binary_path):
    if not (os.path.isfile(binary_path) and os.access(binary_path, os.X_OK)):
        return None
    try:
        out = subprocess.run([binary_path, "--version"], capture_output=True, text=True, timeout=5)
        return out.stdout.strip() or None
    except Exception:
        return None


def remote_has_systemd(ssh):
    cmd = "command -v systemctl >/dev/null 2>&1 && [ -d /run/systemd/system ] && echo yes || echo no"
    _, out, _ = run_ssh(ssh, cmd)
    return out.strip() == "yes"


def local_has_systemd():
    if shutil.which("systemctl") is None:
        return False
    return os.path.isdir("/run/systemd/system")


DOWNLOAD_SNIPPET = (
    "set -e; cd /tmp; "
    "if command -v curl >/dev/null 2>&1; then "
    "curl -fsSL -o {pkg}.tar.gz {url}; "
    "elif command -v wget >/dev/null 2>&1; then "
    "wget -q -O {pkg}.tar.gz {url}; "
    "else echo 'NO_DOWNLOADER' >&2; exit 1; fi"
)


def connect_vps(vps_ip, ssh_port, username, password):
    paramiko = ensure_paramiko()
    print(f"\n[*] Đang kết nối tới VPS {vps_ip}:{ssh_port}...")
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(vps_ip, port=ssh_port, username=username, password=password, timeout=10)
    print("[+] Kết nối SSH thành công!")
    return ssh


def install_and_configure_frps(ssh, bind_port, token):
    run_ssh(ssh, "mkdir -p /etc/frp", check=True)

    installed_version = remote_frp_version(ssh, "/usr/local/bin/frps")
    if installed_version == FRP_VERSION:
        print(f"[*] frps {FRP_VERSION} đã có sẵn trên VPS -> bỏ qua tải lại, chỉ cập nhật cấu hình.")
    else:
        if installed_version:
            print(f"[*] frps hiện tại là bản {installed_version}, khác bản mong muốn {FRP_VERSION} -> cài lại.")
        arch = remote_arch(ssh)
        print(f"[*] Kiến trúc CPU của VPS: {arch}")

        pkg = f"frp_{FRP_VERSION}_linux_{arch}"
        url = f"https://github.com/fatedier/frp/releases/download/v{FRP_VERSION}/{pkg}.tar.gz"

        print("[*] Đang tải và cài đặt FRP trên VPS...")
        commands = [
            DOWNLOAD_SNIPPET.format(pkg=pkg, url=url),
            f"cd /tmp && tar -zxf {pkg}.tar.gz",
            f"cp /tmp/{pkg}/frps /usr/local/bin/frps",
            "chmod +x /usr/local/bin/frps",
        ]
        for cmd in commands:
            run_ssh(ssh, cmd, check=True)

    frps_config = f"""bindAddr = "0.0.0.0"
bindPort = {bind_port}
auth.method = "token"
auth.token = "{token}"
"""
    print("[*] Đang ghi file cấu hình /etc/frp/frps.toml...")
    sftp = ssh.open_sftp()
    with sftp.file('/etc/frp/frps.toml', 'w') as f:
        f.write(frps_config)
    sftp.close()

    if remote_has_systemd(ssh):
        print("[*] Phát hiện systemd -> tạo service frps...")
        unit = """[Unit]
Description=FRP Server
After=network.target

[Service]
Type=simple
ExecStart=/usr/local/bin/frps -c /etc/frp/frps.toml
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
"""
        sftp = ssh.open_sftp()
        with sftp.file('/etc/systemd/system/frps.service', 'w') as f:
            f.write(unit)
        sftp.close()
        run_ssh(ssh, "systemctl daemon-reload && systemctl enable frps && systemctl restart frps", check=True)
    else:
        print("[*] Không có systemd -> chạy nền bằng nohup...")
        run_ssh(ssh, "pkill -9 -f '/usr/local/bin/frps' || true")
        run_ssh(ssh, "nohup /usr/local/bin/frps -c /etc/frp/frps.toml > /var/log/frps.log 2>&1 & disown")

    _, out, _ = run_ssh(
        ssh,
        f"(ss -tulpn 2>/dev/null | grep {bind_port}) || "
        f"(netstat -tulpn 2>/dev/null | grep {bind_port}) || "
        f"ps aux | grep frps | grep -v grep",
    )
    print(f"[+] Trạng thái Server:\n{out}")
    print("[+] Cài đặt VPS Server hoàn tất!")


def setup_server():
    print("\n=== [1] CẤU HÌNH FRP SERVER (TRÊN VPS) ===")
    vps_ip = input("Nhập IP VPS (ví dụ 51.79.xxx.xxx): ").strip()
    ssh_port = prompt_int("Nhập Port SSH (mặc định xxxx): ", default=2277)
    username = input("Nhập Username SSH (mặc định root): ").strip() or "root"
    password = input("Nhập Mật khẩu SSH: ").strip()
    bind_port = prompt_int("Nhập FRP Bind Port (internal port trên VPS, ví dụ 7000): ", default=7000)
    token = input("Nhập Token bảo mật (mặc định Kubark): ").strip() or "Kubark"

    try:
        ssh = connect_vps(vps_ip, ssh_port, username, password)
    except Exception as e:
        print(f"[-] Kết nối thất bại: {e}")
        return

    try:
        install_and_configure_frps(ssh, bind_port, token)
    except Exception as e:
        print(f"[-] Lỗi khi cài đặt server: {e}")
    finally:
        ssh.close()


def download_frpc_local(arch, bin_dir):
    pkg = f"frp_{FRP_VERSION}_linux_{arch}"
    url = f"https://github.com/fatedier/frp/releases/download/v{FRP_VERSION}/{pkg}.tar.gz"
    tmp_tar = f"/tmp/{pkg}.tar.gz"

    if shutil.which("curl"):
        subprocess.check_call(["curl", "-fsSL", "-o", tmp_tar, url])
    elif shutil.which("wget"):
        subprocess.check_call(["wget", "-q", "-O", tmp_tar, url])
    else:
        print("[-] Cần có curl hoặc wget trên máy local để tải frpc.")
        sys.exit(1)

    subprocess.check_call(["tar", "-zxf", tmp_tar, "-C", "/tmp"])
    extracted_bin = f"/tmp/{pkg}/frpc"

    os.makedirs(bin_dir, exist_ok=True)
    final_path = os.path.join(bin_dir, "frpc")
    shutil.copy(extracted_bin, final_path)
    os.chmod(final_path, 0o755)
    return final_path


def ensure_frpc_local(bin_dir):
    frpc_path = os.path.join(bin_dir, "frpc")
    installed_version = local_frp_version(frpc_path)
    if installed_version == FRP_VERSION:
        print(f"[*] frpc {FRP_VERSION} đã có sẵn tại {frpc_path} -> bỏ qua tải lại.")
        return frpc_path

    if installed_version:
        print(f"[*] frpc hiện tại là bản {installed_version}, khác bản mong muốn {FRP_VERSION} -> cài lại.")
    arch = local_arch()
    print(f"[*] Kiến trúc CPU máy local: {arch}")
    print("[*] Đang tải binary frpc...")
    return download_frpc_local(arch, bin_dir)


def build_proxy_snippet(local_port, remote_port, tag):
    return f"""[[proxies]]
name = "service-tcp-{tag}"
type = "tcp"
localIP = "127.0.0.1"
localPort = {local_port}
remotePort = {remote_port}

[[proxies]]
name = "service-udp-{tag}"
type = "udp"
localIP = "127.0.0.1"
localPort = {local_port}
remotePort = {remote_port}
"""


def build_client_base_config(server_addr, server_port, token, confd_dir):
    # Mỗi port dịch vụ nằm trong 1 file riêng dưới confd/ (tính năng "includes" của
    # frp) -> thêm port mới chỉ thêm file mới, không đụng tới port đã cấu hình trước.
    confd_glob = os.path.join(confd_dir, "*.toml").replace("\\", "/")
    return f"""serverAddr = "{server_addr}"
serverPort = {server_port}

auth.method = "token"
auth.token = "{token}"

includes = ["{confd_glob}"]
"""


def deploy_client_config(server_addr, server_port, token, local_port, remote_port, tag):
    is_root = (os.geteuid() == 0) if hasattr(os, "geteuid") else False
    if is_root:
        config_dir = "/etc/frp"
        bin_dir = "/usr/local/bin"
    else:
        config_dir = os.path.expanduser("~/.config/frp")
        bin_dir = os.path.expanduser("~/.local/bin")
        print(f"[*] Không chạy với quyền root -> dùng thư mục người dùng: {config_dir}")

    confd_dir = os.path.join(config_dir, "confd")

    try:
        frpc_path = ensure_frpc_local(bin_dir)
        print(f"[+] frpc sẵn sàng tại: {frpc_path}")
        if bin_dir not in os.environ.get("PATH", "") and not is_root:
            print(f"[!] Lưu ý: thêm '{bin_dir}' vào PATH để gọi 'frpc' trực tiếp (export PATH=\"$PATH:{bin_dir}\").")

        os.makedirs(confd_dir, exist_ok=True)
        config_path = os.path.join(config_dir, "frpc.toml")
        with open(config_path, "w") as f:
            f.write(build_client_base_config(server_addr, server_port, token, confd_dir))

        proxy_path = os.path.join(confd_dir, f"port-{tag}.toml")
        is_update = os.path.exists(proxy_path)
        with open(proxy_path, "w") as f:
            f.write(build_proxy_snippet(local_port, remote_port, tag))
        print(f"[+] Đã {'cập nhật' if is_update else 'thêm mới'} cấu hình cho port {tag} tại {proxy_path}")

        other_ports = sorted(
            p for p in os.listdir(confd_dir)
            if p.startswith("port-") and p.endswith(".toml") and p != f"port-{tag}.toml"
        )
        if other_ports:
            print(f"[*] Các port khác vẫn giữ nguyên, chạy song song: {', '.join(other_ports)}")

        if is_root and local_has_systemd():
            print("[*] Phát hiện systemd (root) -> tạo service frpc...")
            unit = f"""[Unit]
Description=FRP Client
After=network.target

[Service]
Type=simple
ExecStart={frpc_path} -c {config_path}
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
"""
            with open("/etc/systemd/system/frpc.service", "w") as f:
                f.write(unit)
            subprocess.run(["systemctl", "daemon-reload"], check=True)
            subprocess.run(["systemctl", "enable", "frpc"], check=True)
            subprocess.run(["systemctl", "restart", "frpc"], check=True)
            print("[+] Đã khởi động/restart frpc qua systemd (systemctl status frpc).")
        elif (not is_root) and shutil.which("systemctl") and os.environ.get("XDG_RUNTIME_DIR"):
            print("[*] Phát hiện systemd --user -> tạo user service frpc...")
            user_unit_dir = os.path.expanduser("~/.config/systemd/user")
            os.makedirs(user_unit_dir, exist_ok=True)
            unit = f"""[Unit]
Description=FRP Client

[Service]
Type=simple
ExecStart={frpc_path} -c {config_path}
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
"""
            with open(os.path.join(user_unit_dir, "frpc.service"), "w") as f:
                f.write(unit)
            subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
            subprocess.run(["systemctl", "--user", "enable", "frpc"], check=True)
            subprocess.run(["systemctl", "--user", "restart", "frpc"], check=True)
            print("[+] Đã khởi động/restart frpc qua 'systemctl --user' (systemctl --user status frpc).")
        else:
            print("[*] Không có systemd khả dụng -> chạy nền bằng nohup...")
            subprocess.run(["pkill", "-9", "-f", frpc_path], stderr=subprocess.DEVNULL)
            log_path = os.path.join(config_dir, "frpc.log")
            subprocess.Popen(
                f"nohup {frpc_path} -c {config_path} > {log_path} 2>&1 & disown",
                shell=True,
                start_new_session=True,
            )
            print(f"[+] frpc đang chạy nền, log tại: {log_path}")

        print("[+] Hoàn tất cấu hình Client!")
        print(f"[+] Truy cập dịch vụ qua: {server_addr}:{tag}")
    except Exception as e:
        print(f"[-] Lỗi khi cấu hình client: {e}")


def setup_client():
    print("\n=== [2] CẤU HÌNH FRP CLIENT (TRÊN MÁY LOCAL / KALI) ===")

    server_addr = input(f"Nhập IP Public của VPS{default_suffix(DEFAULT_VPS['ip'])}: ").strip() or DEFAULT_VPS["ip"]
    while not server_addr:
        server_addr = prompt_str("Nhập IP Public của VPS: ")

    server_port = prompt_int(
        f"Nhập Control Port public{default_suffix(DEFAULT_VPS['control_public_port'])} "
        "(port này KHÔNG đổi, đã cố định từ lúc tạo VPS): ",
        default=(DEFAULT_VPS["control_public_port"] or None),
    )
    token = input(f"Nhập Token bảo mật{default_suffix(DEFAULT_VPS['token'])}: ").strip() or DEFAULT_VPS["token"]
    while not token:
        token = prompt_str("Nhập Token bảo mật: ")

    local_port, external_port, internal_port = prompt_service_ports()

    deploy_client_config(server_addr, server_port, token, local_port, internal_port, external_port)


def client_config_dir():
    is_root = (os.geteuid() == 0) if hasattr(os, "geteuid") else False
    return "/etc/frp" if is_root else os.path.expanduser("~/.config/frp")


def read_local_frpc_log(config_dir, lines=40):
    # frpc có thể chạy qua systemd (system hoặc --user) hoặc nohup -> thử từng nguồn.
    for cmd, desc in (
        (["journalctl", "-u", "frpc", "-n", str(lines), "--no-pager"], "journalctl -u frpc"),
        (["journalctl", "--user", "-u", "frpc", "-n", str(lines), "--no-pager"], "journalctl --user -u frpc"),
    ):
        try:
            out = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
            if out.returncode == 0 and out.stdout.strip():
                return out.stdout, desc
        except Exception:
            pass

    log_path = os.path.join(config_dir, "frpc.log")
    if os.path.isfile(log_path):
        with open(log_path, "r", errors="ignore") as f:
            return f.read()[-6000:], f"file {log_path}"
    return "", "(không tìm thấy log)"


def verify_client_connection(config_dir, wait_seconds=4):
    print(f"\n[*] Đợi {wait_seconds}s để frpc kịp kết nối lên server, rồi đọc log...")
    time.sleep(wait_seconds)
    log_text, source = read_local_frpc_log(config_dir)
    print(f"[*] Nguồn log: {source}")

    ok_login = "login to server success" in log_text
    ok_proxy = "start proxy success" in log_text
    has_error = (
        "connection refused" in log_text.lower()
        or "login to the server failed" in log_text
        or "i/o timeout" in log_text.lower()
    )

    if ok_login and ok_proxy:
        print("[+] KẾT NỐI THÀNH CÔNG: thấy 'login to server success' + 'start proxy success' trong log.")
        return True

    if has_error:
        print(
            "[-] CHƯA KẾT NỐI ĐƯỢC. Soát lại: token có khớp 2 bên không, "
            "Control Port public có đúng port NAT trỏ vào bindPort trên VPS không, "
            "và frps trên VPS có đang chạy không (bindAddr=0.0.0.0)."
        )
    else:
        print("[-] CHƯA THẤY DẤU HIỆU KẾT NỐI THÀNH CÔNG. Xem log đầy đủ: journalctl -u frpc -e -f")

    print("\n--- Log gần nhất ---")
    print(log_text[-1500:] if log_text else "(không đọc được log)")
    print("---------------------")
    return False


def verify_server_tunnel(ssh, internal_port):
    print(f"\n[*] Kiểm tra từ VPS: thử kết nối tới 127.0.0.1:{internal_port} (xác nhận traffic thật sự chui qua tunnel)...")
    cmd = (
        f"(nc -zv 127.0.0.1 {internal_port} 2>&1) || "
        f"(timeout 3 bash -c 'echo > /dev/tcp/127.0.0.1/{internal_port}' 2>&1 && echo 'TCP OK') || "
        f"echo 'KHÔNG KẾT NỐI ĐƯỢC TỚI PORT NỘI BỘ NÀY'"
    )
    _, out, err = run_ssh(ssh, cmd)
    print(f"[*] Kết quả: {(out or err).strip()}")


def first_time_pairing():
    print("\n=== [3] THIẾT LẬP LẦN ĐẦU: SERVER + CLIENT TRONG 1 LẦN (CÓ KIỂM TRA KẾT NỐI) ===")
    print("[*] Bước 1/3: Cài & cấu hình FRP Server trên VPS qua SSH.")
    vps_ip = input(f"Nhập IP VPS{default_suffix(DEFAULT_VPS['ip'])}: ").strip() or DEFAULT_VPS["ip"]
    while not vps_ip:
        vps_ip = prompt_str("Nhập IP VPS: ")

    ssh_port = prompt_int("Nhập Port SSH (mặc định 2277): ", default=2277)
    username = input("Nhập Username SSH (mặc định root): ").strip() or "root"
    password = input("Nhập Mật khẩu SSH: ").strip()
    bind_port = prompt_int("Nhập bindPort NỘI BỘ trên VPS cho control (ví dụ 7000): ", default=7000)

    token = input(f"Nhập Token bảo mật{default_suffix(DEFAULT_VPS['token'])}: ").strip() or DEFAULT_VPS["token"]
    while not token:
        token = prompt_str("Nhập Token bảo mật: ")

    try:
        ssh = connect_vps(vps_ip, ssh_port, username, password)
    except Exception as e:
        print(f"[-] Kết nối SSH thất bại: {e}")
        return

    try:
        install_and_configure_frps(ssh, bind_port, token)

        print("\n[*] Bước 2/3: Cấu hình FRP Client ngay trên máy đang chạy script này.")
        print(
            f"[*] Control Port PUBLIC là port NAT (của VPS, KHÔNG phải của Kali) trỏ vào "
            f"bindPort={bind_port} vừa cài ở Bước 1 — port này cố định, lấy 1 lần từ panel VPS."
        )
        control_public_port = prompt_int("Nhập Control Port PUBLIC: ")

        local_port, external_port, internal_port = prompt_service_ports()

        deploy_client_config(vps_ip, control_public_port, token, local_port, internal_port, external_port)

        print("\n[*] Bước 3/3: Kiểm tra kết nối Client <-> Server.")
        client_ok = verify_client_connection(client_config_dir())

        if client_ok:
            verify_server_tunnel(ssh, internal_port)
        else:
            print("[*] Bỏ qua bước kiểm tra phía VPS vì client chưa báo kết nối thành công.")
    except Exception as e:
        print(f"[-] Lỗi trong quá trình thiết lập: {e}")
    finally:
        ssh.close()


def restart_frpc_after_change(config_dir):
    """Restart frpc theo đúng cách nó đang chạy (systemd / systemd --user / nohup),
    dùng cho các thao tác gỡ config để áp dụng thay đổi ngay, không cần chạy lại
    toàn bộ deploy_client_config()."""
    is_root = (os.geteuid() == 0) if hasattr(os, "geteuid") else False

    if is_root and os.path.isfile("/etc/systemd/system/frpc.service"):
        subprocess.run(["systemctl", "restart", "frpc"], check=False)
        print("[+] Đã restart frpc (systemd).")
        return

    user_unit = os.path.expanduser("~/.config/systemd/user/frpc.service")
    if os.path.isfile(user_unit):
        subprocess.run(["systemctl", "--user", "restart", "frpc"], check=False)
        print("[+] Đã restart frpc (systemd --user).")
        return

    bin_dir = "/usr/local/bin" if is_root else os.path.expanduser("~/.local/bin")
    frpc_path = os.path.join(bin_dir, "frpc")
    config_path = os.path.join(config_dir, "frpc.toml")
    if not os.path.isfile(frpc_path):
        print("[-] Không tìm thấy frpc binary để restart, bạn tự chạy lại thủ công.")
        return

    subprocess.run(["pkill", "-9", "-f", frpc_path], stderr=subprocess.DEVNULL)
    log_path = os.path.join(config_dir, "frpc.log")
    subprocess.Popen(
        f"nohup {frpc_path} -c {config_path} > {log_path} 2>&1 & disown",
        shell=True,
        start_new_session=True,
    )
    print(f"[+] Đã restart frpc (nohup), log tại: {log_path}")


def remove_one_port():
    print("\n=== [4] GỠ 1 PORT DỊCH VỤ (KHÔNG MUỐN EXPOSE NỮA) ===")
    config_dir = client_config_dir()
    confd_dir = os.path.join(config_dir, "confd")

    if not os.path.isdir(confd_dir):
        print(f"[-] Không thấy thư mục cấu hình: {confd_dir} (chưa cấu hình port nào trên máy này).")
        return

    port_files = sorted(f for f in os.listdir(confd_dir) if f.startswith("port-") and f.endswith(".toml"))
    if not port_files:
        print("[-] Hiện không có port nào đang cấu hình.")
        return

    print("[*] Các port đang cấu hình (lấy tên cổng ngoài từ lúc tạo):")
    for i, f in enumerate(port_files, start=1):
        print(f"  {i}. {f}")

    raw = input(f"Chọn port cần gỡ (1-{len(port_files)}): ").strip()
    try:
        chosen = port_files[int(raw) - 1]
    except (ValueError, IndexError):
        print("[-] Lựa chọn không hợp lệ.")
        return

    confirm = input(f"Xoá '{chosen}' và dừng expose port này? (y/N): ").strip().lower()
    if confirm != "y":
        print("[*] Đã hủy, không xoá gì.")
        return

    os.remove(os.path.join(confd_dir, chosen))
    print(f"[+] Đã xoá {chosen}.")
    restart_frpc_after_change(config_dir)


def teardown_client():
    print("\n=== [5] DỪNG & GỠ TOÀN BỘ CLIENT (DÙNG KHI ĐỔI SANG VPS KHÁC) ===")
    config_dir = client_config_dir()
    is_root = (os.geteuid() == 0) if hasattr(os, "geteuid") else False

    if is_root and os.path.isfile("/etc/systemd/system/frpc.service"):
        subprocess.run(["systemctl", "stop", "frpc"], check=False)
        subprocess.run(["systemctl", "disable", "frpc"], check=False)
        print("[+] Đã dừng & tắt tự khởi động frpc (systemd).")

    user_unit = os.path.expanduser("~/.config/systemd/user/frpc.service")
    if os.path.isfile(user_unit):
        subprocess.run(["systemctl", "--user", "stop", "frpc"], check=False)
        subprocess.run(["systemctl", "--user", "disable", "frpc"], check=False)
        print("[+] Đã dừng & tắt tự khởi động frpc (systemd --user).")

    bin_dir = "/usr/local/bin" if is_root else os.path.expanduser("~/.local/bin")
    frpc_path = os.path.join(bin_dir, "frpc")
    subprocess.run(["pkill", "-9", "-f", frpc_path], stderr=subprocess.DEVNULL)
    print("[+] Đã dừng tiến trình frpc (nếu đang chạy nền bằng nohup).")

    confirm = input(f"Xoá luôn thư mục cấu hình {config_dir} (mất hết các port đã lưu)? (y/N): ").strip().lower()
    if confirm == "y":
        shutil.rmtree(config_dir, ignore_errors=True)
        print(f"[+] Đã xoá {config_dir}.")
    else:
        print(f"[*] Giữ lại {config_dir}, chỉ dừng service — có thể dùng lại config này sau.")

    print("[+] Client đã dừng. Có thể chạy lại chế độ 2/3 để nối sang VPS khác.")


def teardown_server():
    print("\n=== [6] GỠ FRPS TRÊN VPS (QUA SSH) ===")
    vps_ip = prompt_str("Nhập IP VPS: ")
    ssh_port = prompt_int("Nhập Port SSH: ")
    username = input("Nhập Username SSH (mặc định root): ").strip() or "root"
    password = input("Nhập Mật khẩu SSH: ").strip()

    try:
        ssh = connect_vps(vps_ip, ssh_port, username, password)
    except Exception as e:
        print(f"[-] Kết nối SSH thất bại: {e}")
        return

    try:
        if remote_has_systemd(ssh):
            run_ssh(ssh, "systemctl stop frps || true")
            run_ssh(ssh, "systemctl disable frps || true")
            print("[+] Đã dừng & tắt tự khởi động frps (systemd).")
        else:
            run_ssh(ssh, "pkill -9 -f '/usr/local/bin/frps' || true")
            print("[+] Đã dừng frps (nohup).")

        confirm = input("Xoá luôn binary + config frps trên VPS (/usr/local/bin/frps, /etc/frp)? (y/N): ").strip().lower()
        if confirm == "y":
            run_ssh(ssh, "rm -f /usr/local/bin/frps && rm -rf /etc/frp")
            print("[+] Đã xoá binary + config frps trên VPS.")
        else:
            print("[*] Giữ lại binary + config frps trên VPS, chỉ dừng service.")
    finally:
        ssh.close()


def main():
    print("=========================================")
    print("   QUẢN LÝ & TỰ ĐỘNG HÓA FRP (ONE-FILE)   ")
    print("=========================================")
    print("1. Cài đặt & Cấu hình Server (VPS)")
    print("2. Cấu hình Client (Local / Kali)")
    print("3. Thiết lập lần đầu Server + Client (có kiểm tra kết nối)")
    print("4. Gỡ 1 port dịch vụ (không muốn expose nữa)")
    print("5. Dừng & gỡ toàn bộ Client (đổi sang VPS khác)")
    print("6. Gỡ frps trên VPS cũ (qua SSH)")
    choice = input("Chọn chế độ (1-6): ").strip()

    if choice == "1":
        setup_server()
    elif choice == "2":
        setup_client()
    elif choice == "3":
        first_time_pairing()
    elif choice == "4":
        remove_one_port()
    elif choice == "5":
        teardown_client()
    elif choice == "6":
        teardown_server()
    else:
        print("Lựa chọn không hợp lệ!")


if __name__ == "__main__":
    main()
