Hướng dẫn cài đặt Fast Reverse Proxy (FRP) cho Game Server
Tài liệu này hướng dẫn cách cấu hình FRP để mở port Game Server (Ví dụ: CS:GO, Port 27015 TCP/UDP) từ máy Local (chạy sau NAT/Router) thông qua một VPS có IP Public.

📌 Kiến trúc hệ thống
FRP Server (frps): Cài đặt trên VPS (IP Public). Đóng vai trò cầu nối, nhận traffic từ Internet và đẩy về máy Local.

FRP Client (frpc): Cài đặt trên máy Game Server Local (VD: Kali Linux, Ubuntu). Đóng vai trò duy trì đường hầm (tunnel) kết nối lên VPS.

⚠️ Yêu cầu chuẩn bị
Trên trang quản trị của nhà cung cấp VPS (và tường lửa nội bộ ufw/iptables nếu có), bắt buộc phải mở các port sau:

Port 7000 (TCP): Dùng để Client và Server giao tiếp với nhau.

Port 27015 (TCP & UDP): Dùng cho người chơi bên ngoài kết nối vào Game.

Phần 1: Cài đặt FRP Server (Trên VPS)
1. Tải và chuẩn bị file thực thi
SSH vào VPS và chạy các lệnh sau (phiên bản v0.58.1):

```
wget https://github.com/fatedier/frp/releases/download/v0.58.1/frp_0.58.1_linux_amd64.tar.gz
tar -zxvf frp_0.58.1_linux_amd64.tar.gz
cd frp_0.58.1_linux_amd64

# Chép file thực thi vào hệ thống và cấp quyền
sudo cp frps /usr/local/bin/
sudo chmod +x /usr/local/bin/frps
```
2. Cấu hình Server (frps.toml)
Tạo thư mục cấu hình:

```
sudo mkdir -p /etc/frp
```
Tạo và ghi file cấu hình frps.toml:

```
sudo cat <<EOF > /etc/frp/frps.toml
bindPort = 7000

auth.method = "token"
auth.token = "Kubark"
EOF
3. Tạo Service chạy ngầm (systemd)
Bash
sudo cat <<EOF > /etc/systemd/system/frps.service
[Unit]
Description=FRP Server Service
After=network.target

[Service]
Type=simple
User=root
Restart=on-failure
RestartSec=5s
ExecStart=/usr/local/bin/frps -c /etc/frp/frps.toml

[Install]
WantedBy=multi-user.target
EOF
```
4. Kích hoạt và Khởi động Server
```
sudo systemctl daemon-reload
sudo systemctl enable frps
sudo systemctl start frps
sudo systemctl status frps
```
(Kiểm tra trạng thái báo active (running) là thành công).

Phần 2: Cài đặt FRP Client (Trên máy Local/Game Server)
1. Tải và chuẩn bị file thực thi
Mở terminal trên máy Local và chạy:

```
wget https://github.com/fatedier/frp/releases/download/v0.58.1/frp_0.58.1_linux_amd64.tar.gz
tar -zxvf frp_0.58.1_linux_amd64.tar.gz
cd frp_0.58.1_linux_amd64

# Chép file thực thi vào hệ thống và cấp quyền
sudo cp frpc /usr/local/bin/
sudo chmod +x /usr/local/bin/frpc
```
2. Cấu hình Client (frpc.toml)
Tạo thư mục cấu hình:

```
sudo mkdir -p /etc/frp
```
Tạo và ghi file cấu hình frpc.toml (Thay IP_PUBLIC_CUA_VPS bằng IP thật của VPS):

```
sudo cat <<EOF > /etc/frp/frpc.toml
serverAddr = "IP_PUBLIC_CUA_VPS"
serverPort = 7000

auth.method = "token"
auth.token = "Kubark"

[[proxies]]
name = "game-tcp-27015"
type = "tcp"
localIP = "127.0.0.1" 
localPort = 27015
remotePort = 27015

[[proxies]]
name = "game-udp-27015"
type = "udp"
localIP = "127.0.0.1"
localPort = 27015
remotePort = 27015
EOF
```
3. Tạo Service chạy ngầm (systemd)
```
sudo cat <<EOF > /etc/systemd/system/frpc.service
[Unit]
Description=FRP Client Service
After=network.target

[Service]
Type=simple
User=root
Restart=on-failure
RestartSec=5s
ExecStart=/usr/local/bin/frpc -c /etc/frp/frpc.toml

[Install]
WantedBy=multi-user.target
EOF
```
4. Kích hoạt và Khởi động Client
```
sudo systemctl daemon-reload
sudo systemctl enable frpc
sudo systemctl start frpc
sudo systemctl status frpc
```
(Kiểm tra trạng thái báo active (running) là thành công).

🔍 Kiểm tra lỗi (Troubleshooting)
Để kiểm tra xem Client đã kết nối thành công lên Server và đục port qua NAT chưa, sử dụng lệnh xem log trực tiếp trên máy Local:

```
sudo journalctl -u frpc.service -e -f
```
Các dấu hiệu kết nối thành công:

login to server success: Token xác thực chính xác, hai bên đã kết nối.

start proxy success: Port đã được ánh xạ thành công.

incoming a new work connection: Đang có người chơi từ Internet kết nối vào Server Game.
