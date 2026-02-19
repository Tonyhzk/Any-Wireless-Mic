"""HTTPS 自签名证书生成"""

import ipaddress
import datetime as dt_module

from cryptography import x509
from cryptography.x509.oid import NameOID
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa


def generate_cert(cert_path, key_path, local_ip, all_ips=None, log_callback=None):
    """
    生成自签名证书（含 SAN，兼容 macOS）

    Args:
        cert_path: 证书文件路径
        key_path: 私钥文件路径
        local_ip: 主 IP 地址字符串
        all_ips: 所有本地 IP 列表
        log_callback: 日志回调函数 (message, level)
    """
    def log(msg, level="INFO"):
        if log_callback:
            log_callback(msg, level)

    try:
        log("正在生成自签名证书 (cryptography)...", "INFO")

        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

        # 构建 SAN 列表
        alt_names = []
        added_ips = set()

        try:
            ip_obj = ipaddress.ip_address(local_ip)
            alt_names.append(x509.IPAddress(ip_obj))
            added_ips.add(str(ip_obj))
        except:
            pass

        if all_ips:
            for ip in all_ips:
                if ip not in added_ips:
                    try:
                        ip_obj = ipaddress.ip_address(ip)
                        alt_names.append(x509.IPAddress(ip_obj))
                        added_ips.add(ip)
                    except:
                        pass

        alt_names.append(x509.DNSName("localhost"))

        subject = x509.Name([
            x509.NameAttribute(NameOID.COUNTRY_NAME, "CN"),
            x509.NameAttribute(NameOID.STATE_OR_PROVINCE_NAME, "State"),
            x509.NameAttribute(NameOID.LOCALITY_NAME, "City"),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "MobileMic"),
            x509.NameAttribute(NameOID.COMMON_NAME, local_ip),
        ])

        now = dt_module.datetime.utcnow()
        cert = (
            x509.CertificateBuilder()
            .subject_name(subject)
            .issuer_name(subject)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now)
            .not_valid_after(now + dt_module.timedelta(days=3650))
            .add_extension(x509.SubjectAlternativeName(alt_names), critical=False)
            .sign(key, hashes.SHA256())
        )

        with open(cert_path, "wb") as f:
            f.write(cert.public_bytes(serialization.Encoding.PEM))

        with open(key_path, "wb") as f:
            f.write(key.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.TraditionalOpenSSL,
                encryption_algorithm=serialization.NoEncryption(),
            ))

        log("证书生成成功", "SUCCESS")
        return True

    except Exception as e:
        log(f"证书生成失败: {e}", "ERROR")
        return False
