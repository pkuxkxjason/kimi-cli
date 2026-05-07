#!/usr/bin/env python3
"""
Kimi CLI - 极简命令行工具，通过 stdin/stdout 与 kimi.com 交互
支持多轮对话、会话持久化、微信扫码登录，二维码通过邮件发送

Usage:
    echo "你好" | python3 kimi.py
    cat question.txt | python3 kimi.py

配置:
    在 ~/.kimi_cli_config.json 中配置邮箱授权码:
    {
        "email_password": "你的163邮箱授权码"
    }
"""

import sys
import json
import os
import base64
import time
import smtplib
from pathlib import Path
from dataclasses import dataclass
from typing import Optional, List, Dict
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.image import MIMEImage

try:
    from playwright.sync_api import sync_playwright, Page
except ImportError:
    print(json.dumps({
        "error": "需要安装依赖: pip3 install playwright",
        "install_command": "pip3 install playwright && playwright install chromium"
    }, ensure_ascii=False))
    sys.exit(1)


SESSION_FILE = Path.home() / ".kimi_cli_session.json"
CONFIG_FILE = Path.home() / ".kimi_cli_config.json"
KIMI_URL = "https://kimi.com"

EMAIL_SENDER = "zzm9981@163.com"
EMAIL_RECEIVER = "zzm9981@163.com"
EMAIL_SMTP_SERVER = "smtp.163.com"
EMAIL_SMTP_PORT = 465


def load_config() -> Dict:
    """加载配置文件"""
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def get_email_password() -> Optional[str]:
    """获取邮箱授权码"""
    password = os.environ.get("KIMI_EMAIL_PASSWORD")
    if password:
        return password
    config = load_config()
    return config.get("email_password")


def send_email_with_qr(qr_image_bytes: bytes) -> bool:
    """发送带二维码图片的邮件"""
    password = get_email_password()
    if not password:
        print(json.dumps({
            "status": "error",
            "message": "需要邮箱授权码",
            "instruction": f"请在 {CONFIG_FILE} 中配置 email_password"
        }, ensure_ascii=False))
        return False

    try:
        msg = MIMEMultipart()
        msg['From'] = EMAIL_SENDER
        msg['To'] = EMAIL_RECEIVER
        msg['Subject'] = 'Kimi CLI 登录二维码'

        body = """
请在手机上使用微信扫描附件中的二维码完成 Kimi 登录。

此二维码用于 Kimi CLI 命令行工具的微信登录。
"""
        msg.attach(MIMEText(body, 'plain', 'utf-8'))

        image = MIMEImage(qr_image_bytes)
        image.add_header('Content-Disposition', 'attachment', filename='kimi_qr.png')
        msg.attach(image)

        server = smtplib.SMTP_SSL(EMAIL_SMTP_SERVER, EMAIL_SMTP_PORT)
        server.login(EMAIL_SENDER, password)
        server.sendmail(EMAIL_SENDER, EMAIL_RECEIVER, msg.as_string())
        server.quit()

        return True
    except Exception as e:
        print(json.dumps({
            "status": "error",
            "message": f"发送邮件失败: {str(e)}"
        }, ensure_ascii=False))
        return False


@dataclass
class Session:
    messages: List[Dict] = None
    cookies: List[Dict] = None
    is_logged_in: bool = False

    def __post_init__(self):
        if self.messages is None:
            self.messages = []

    def to_dict(self):
        return {
            "messages": self.messages,
            "cookies": self.cookies,
            "is_logged_in": self.is_logged_in
        }

    @classmethod
    def from_dict(cls, data: dict):
        return cls(
            messages=data.get("messages", []),
            cookies=data.get("cookies"),
            is_logged_in=data.get("is_logged_in", False)
        )


def load_session() -> Session:
    if SESSION_FILE.exists():
        try:
            with open(SESSION_FILE, 'r', encoding='utf-8') as f:
                return Session.from_dict(json.load(f))
        except Exception:
            pass
    return Session()


def save_session(session: Session):
    with open(SESSION_FILE, 'w', encoding='utf-8') as f:
        json.dump(session.to_dict(), f, ensure_ascii=False, indent=2)


def check_login_dialog(page: Page, debug: bool = False) -> bool:
    """检查是否有登录对话框 - 检测登录弹窗容器"""
    try:
        # 登录弹窗的特征：同时包含"微信扫码登录"文字和二维码canvas
        # 或者包含"手机号快捷登录"文字

        # 首先检查是否有登录弹窗容器
        modal_selectors = [
            "[class*='modal']:has(text=微信扫码登录)",
            "[class*='dialog']:has(text=微信扫码登录)",
            "[class*='login-dialog']",
            "[class*='login-modal']",
        ]

        for selector in modal_selectors:
            try:
                element = page.locator(selector).first
                if element.count() > 0 and element.is_visible():
                    if debug:
                        print(json.dumps({
                            "status": "debug",
                            "message": f"检测到登录弹窗: {selector}"
                        }, ensure_ascii=False), flush=True)
                    return True
            except:
                continue

        # 备选：检查微信扫码登录文字（这个只在登录弹窗中出现）
        try:
            wechat_text = page.locator("text=微信扫码登录").first
            if wechat_text.count() > 0 and wechat_text.is_visible():
                # 确认这个文字在可视区域内
                if debug:
                    print(json.dumps({
                        "status": "debug",
                        "message": "检测到微信扫码登录文字"
                    }, ensure_ascii=False), flush=True)
                return True
        except:
            pass

        if debug:
            print(json.dumps({
                "status": "debug",
                "message": "登录框已关闭"
            }, ensure_ascii=False), flush=True)

        return False
    except Exception as e:
        if debug:
            print(json.dumps({
                "status": "debug",
                "message": f"检测出错: {str(e)}"
            }, ensure_ascii=False), flush=True)
        return False


def take_screenshot(page: Page, name: str):
    """截图保存用于调试"""
    try:
        screenshot_path = f"/tmp/kimi_debug_{name}.png"
        page.screenshot(path=screenshot_path, full_page=True)
        print(json.dumps({
            "status": "debug",
            "message": f"已截图保存到: {screenshot_path}"
        }, ensure_ascii=False), flush=True)
    except Exception as e:
        print(json.dumps({
            "status": "debug",
            "message": f"截图失败: {str(e)}"
        }, ensure_ascii=False), flush=True)


def get_qr_code_image(page: Page) -> Optional[bytes]:
    """获取登录二维码"""
    try:
        qr_canvas = page.locator("canvas").first
        if qr_canvas.count() > 0:
            return qr_canvas.screenshot()
    except Exception as e:
        print(f"获取二维码失败: {e}", file=sys.stderr)
    return None


def close_modal_dialog(page: Page) -> bool:
    """关闭模态对话框/广告弹窗"""
    try:
        # 常见的关闭按钮选择器（包括"稍后再说"）
        close_selectors = [
            "text=稍后再说",
            "text=关闭",
            "text=我知道了",
            "text=跳过",
            "button[class*='close']",
            "[class*='modal'] [class*='close']",
            "[class*='dialog'] [class*='close']",
            "svg[name='Close']",
            ".modal-close",
            ".dialog-close",
            "[aria-label='关闭']"
        ]

        closed = False
        for selector in close_selectors:
            try:
                close_btn = page.locator(selector).first
                if close_btn.count() > 0 and close_btn.is_visible():
                    close_btn.click()
                    time.sleep(1)
                    print(json.dumps({
                        "status": "info",
                        "message": f"已关闭弹窗: {selector}"
                    }, ensure_ascii=False), flush=True)
                    closed = True
            except:
                continue

        # 如果没有找到关闭按钮，尝试按 ESC 键
        if not closed:
            try:
                page.keyboard.press("Escape")
                time.sleep(0.5)
            except:
                pass

        return closed
    except:
        return False


def wait_for_login(page: Page, timeout: int = 120) -> bool:
    """轮询检测扫码登录状态 - 检测到登录框消失后，再确认几次"""
    print(json.dumps({
        "status": "waiting_login",
        "message": "等待扫码...",
        "timeout": timeout
    }, ensure_ascii=False), flush=True)

    waited = 0
    check_interval = 5  # 每5秒检查一次
    confirmed = 0  # 连续确认次数

    while waited < timeout:
        time.sleep(check_interval)
        waited += check_interval

        # 检查登录状态（不要刷新页面，否则二维码会丢失）
        try:
            has_login = check_login_dialog(page)

            if not has_login:
                # 登录框消失，连续确认3次
                confirmed += 1
                if confirmed >= 3:
                    print(json.dumps({
                        "status": "login_success",
                        "message": "登录成功",
                        "waited": waited
                    }, ensure_ascii=False), flush=True)
                    return True
            else:
                # 登录框还在，重置确认计数
                confirmed = 0

            # 每10秒输出一次进度
            if waited % 10 == 0 and waited > 0:
                print(json.dumps({
                    "status": "waiting_login",
                    "message": f"已等待 {waited} 秒，请尽快扫码...",
                    "elapsed": waited
                }, ensure_ascii=False), flush=True)

        except Exception as e:
            continue

    # 超时
    print(json.dumps({
        "status": "timeout",
        "message": f"等待超时（{timeout}秒），未完成登录"
    }, ensure_ascii=False), flush=True)
    return False


def send_message(page: Page, message: str) -> tuple[bool, Optional[str]]:
    """
    发送消息并获取回复
    返回: (是否成功, 回复内容或错误信息)
    """
    try:
        # 找到输入框
        input_box = page.locator(".chat-input-editor").first
        if input_box.count() == 0:
            return False, "找不到输入框"

        # 输入并发送
        input_box.scroll_into_view_if_needed()
        input_box.click()
        time.sleep(0.3)
        input_box.fill(message)
        time.sleep(0.3)
        input_box.press("Enter")
        time.sleep(2)

        # 检查是否需要登录（最多等待10秒）
        login_check_count = 0
        while check_login_dialog(page) and login_check_count < 5:
            time.sleep(2)
            login_check_count += 1

        if check_login_dialog(page):
            return False, "__NEED_LOGIN__"

        # 等待回复完成
        print(json.dumps({
            "status": "waiting",
            "message": "等待 Kimi 回复..."
        }, ensure_ascii=False), flush=True)

        time.sleep(5)  # 给 Kimi 一些时间开始生成

        max_wait = 120  # 最多等待 120 秒
        waited = 0
        check_interval = 3

        while waited < max_wait:
            # 检查是否还在生成中
            stop_btn_count = page.locator("text=停止生成").count()
            if stop_btn_count == 0:
                # 再等待一下确保内容已渲染
                time.sleep(2)
                print(json.dumps({
                    "status": "waiting",
                    "message": "回复生成完成"
                }, ensure_ascii=False), flush=True)
                break

            time.sleep(check_interval)
            waited += check_interval

            # 每 10 秒输出一次进度
            if waited % 10 == 0:
                print(json.dumps({
                    "status": "waiting",
                    "message": f"已等待回复 {waited} 秒..."
                }, ensure_ascii=False), flush=True)

        # 获取回复 - Kimi 的回复在对话气泡中
        selectors = [
            "[class*='message']:has([class*='assistant']) .rich-text",
            "[class*='assistant'] .rich-text",
            ".rich-text",
        ]

        for selector in selectors:
            try:
                elements = page.locator(selector).all()
                if elements:
                    # 获取最后一个元素的内容（最新的回复）
                    text = elements[-1].inner_text().strip()
                    if text and text != message and len(text) > 10:
                        # 清理内容，过滤页面噪音
                        lines = []
                        for line in text.split('\n'):
                            line = line.strip()
                            # 跳过明显的页面元素
                            if any(skip in line for skip in [
                                '获取应用程序', '升级套餐', '内容由AI生成',
                                'K2.6 快速', '尽管问，带图也行', '发送',
                                '查看全部', '编辑', '复制', '分享'
                            ]):
                                continue
                            if line:
                                lines.append(line)

                        if lines:
                            return True, '\n'.join(lines[:20])
            except:
                continue

        # 备选：从页面文本中提取回复
        try:
            all_text = page.locator("body").inner_text()
            if message in all_text:
                # 找到用户问题后的内容
                idx = all_text.find(message)
                if idx != -1:
                    after = all_text[idx + len(message):].strip()
                    # 提取前1000字符作为回复
                    if len(after) > 50:
                        # 过滤噪音
                        lines = []
                        for line in after.split('\n'):
                            line = line.strip()
                            if any(skip in line for skip in [
                                '获取应用程序', '升级套餐', '内容由AI生成',
                                'K2.6 快速', '尽管问，带图也行', '发送'
                            ]):
                                continue
                            if line and len(line) > 2:
                                lines.append(line)
                        if lines:
                            return True, '\n'.join(lines[:30])
        except:
            pass

        return False, "无法获取回复内容"

    except Exception as e:
        return False, f"错误: {str(e)}"


def main():
    # 读取输入
    input_text = sys.stdin.read().strip()
    if not input_text:
        print(json.dumps({"error": "请输入问题"}, ensure_ascii=False))
        sys.exit(1)

    session = load_session()

    with sync_playwright() as p:
        # 启动浏览器（无头模式）
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1280, "height": 800})
        page = context.new_page()

        try:
            # 访问页面（缩短等待时间）
            page.goto(KIMI_URL, wait_until="domcontentloaded", timeout=20000)
            time.sleep(3)

            # 尝试发送消息
            success, response = send_message(page, input_text)

            # 如果需要登录
            if not success and response == "__NEED_LOGIN__":
                # 获取并发送二维码
                qr_image = get_qr_code_image(page)
                if not qr_image:
                    print(json.dumps({
                        "error": "无法获取登录二维码"
                    }, ensure_ascii=False))
                    sys.exit(1)

                if not send_email_with_qr(qr_image):
                    sys.exit(1)

                # 提示用户扫码
                print(json.dumps({
                    "status": "waiting_login",
                    "message": "二维码已发送",
                    "instruction": f"请查看邮件 {EMAIL_RECEIVER}，用微信扫码！程序会自动检测（请勿关闭终端）..."
                }, ensure_ascii=False), flush=True)

                # 自动检测登录状态（会等待最多120秒）
                print(json.dumps({
                    "status": "waiting_login",
                    "message": "等待扫码中..."
                }, ensure_ascii=False), flush=True)

                # 自动检测登录状态
                if not wait_for_login(page):
                    print(json.dumps({
                        "error": "登录超时"
                    }, ensure_ascii=False))
                    sys.exit(1)

                # 关闭广告/弹窗（点击"稍后再说"等）
                time.sleep(2)
                close_modal_dialog(page)

                # 等待页面稳定
                time.sleep(3)

                # 保存登录状态
                try:
                    session.cookies = context.cookies()
                    session.is_logged_in = True
                    save_session(session)
                except Exception as e:
                    print(json.dumps({
                        "status": "warning",
                        "message": f"保存会话失败: {str(e)}"
                    }, ensure_ascii=False), flush=True)

                # 尝试直接发送消息
                success, response = send_message(page, input_text)

                # 如果还有登录框，可能是另一个弹窗，再关闭一次
                if not success and response == "__NEED_LOGIN__":
                    print(json.dumps({
                        "status": "waiting_login",
                        "message": "检测到登录框，尝试关闭..."
                    }, ensure_ascii=False), flush=True)

                    # 尝试关闭弹窗
                    close_modal_dialog(page)
                    time.sleep(2)

                    # 再次尝试发送
                    success, response = send_message(page, input_text)

                # 如果还有登录框，再等待一下
                if not success and response == "__NEED_LOGIN__":
                    print(json.dumps({
                        "status": "waiting_login",
                        "message": "还有登录框，再关闭一次..."
                    }, ensure_ascii=False), flush=True)

                    # 再关闭一次弹窗
                    close_modal_dialog(page)
                    time.sleep(3)

                    # 再次尝试发送
                    success, response = send_message(page, input_text)

            if success:
                # 保存会话
                session.messages.append({"role": "user", "content": input_text})
                session.messages.append({"role": "assistant", "content": response})
                save_session(session)

                print(json.dumps({
                    "status": "success",
                    "query": input_text,
                    "response": response,
                    "message_count": len(session.messages)
                }, ensure_ascii=False, indent=2))
            else:
                print(json.dumps({
                    "error": response or "请求失败"
                }, ensure_ascii=False))

        except Exception as e:
            print(json.dumps({
                "error": f"执行错误: {str(e)}"
            }, ensure_ascii=False))

        finally:
            browser.close()


if __name__ == "__main__":
    main()
