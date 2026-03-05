import requests
import json
import logging
import time
import hmac
import hashlib
import base64
import urllib.parse

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')


class DingTalkAlerter:
    def __init__(self, webhook_url: str, secret: str = None):
        """
        初始化钉钉机器人
        :param webhook_url: 机器人的 webhook 地址
        :param secret: 如果安全设置选了加签，传入密钥；如果选了关键词，传 None
        """
        self.webhook_url = webhook_url
        self.secret = secret

    def _get_signed_url(self) -> str:
        """生成带时间戳和签名的安全请求 URL"""
        if not self.secret:
            return self.webhook_url

        timestamp = str(round(time.time() * 1000))
        secret_enc = self.secret.encode('utf-8')
        string_to_sign = '{}\n{}'.format(timestamp, self.secret)
        string_to_sign_enc = string_to_sign.encode('utf-8')

        hmac_code = hmac.new(secret_enc, string_to_sign_enc, digestmod=hashlib.sha256).digest()
        sign = urllib.parse.quote_plus(base64.b64encode(hmac_code))

        return f"{self.webhook_url}&timestamp={timestamp}&sign={sign}"

    def send_anomaly_alert(self, item_name: str, current_price: float, obi: float, sdr: float, signal_type: str):
        """
        向钉钉发送格式化的 Markdown 警报
        """
        # 设置 Markdown 标题颜色：建仓用红色醒目，砸盘用绿色或橙色
        title_color = "#FF0000" if "建仓" in signal_type else "#FFA500"

        markdown_text = f"""
### <font color='{title_color}'>🚨 CS 盘口异动预警</font>

**饰品名称:** `{item_name}`
**当前买一价:** `¥ {current_price}`
**判定动作:** **{signal_type}**

---
#### 深度特征数据：
* **订单簿失衡 (OBI):** `{obi:.2f}` *(>0为买盘强，<0为卖盘强)*
* **供给锐减率 (SDR):** `{sdr * 100:.2f}%` *(越高代表抛压消失越快)*
* **预警时间:** `{time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())}`

> ⚠️ **系统提示:** 请结合实际盘面决策，注意 7 天交易冷却期风险！
"""

        payload = {
            "msgtype": "markdown",
            "markdown": {
                "title": "CS 饰品异动预警",  # 手机通知栏显示的文字（须包含你设置的关键词）
                "text": markdown_text
            }
        }

        url = self._get_signed_url()
        headers = {'Content-Type': 'application/json'}

        try:
            response = requests.post(url, data=json.dumps(payload), headers=headers, timeout=5)
            if response.status_code == 200 and response.json().get('errcode') == 0:
                logging.info(f"钉钉预警发送成功: {item_name}")
            else:
                logging.error(f"钉钉预警发送失败: {response.text}")
        except Exception as e:
            logging.error(f"网络异常导致钉钉预警发送失败: {e}")


# ================= 测试运行 =================
if __name__ == "__main__":
    # 替换为你实际申请的 webhook url
    WEBHOOK = "https://oapi.dingtalk.com/robot/send?access_token=1b6960d9729f63f53042c1c3daa06cb5b61abd77852529a90097e977c6b30db9"
    SECRET = "SEC070029a84acfd04995ae3ecc156d17016ace81b14b534be85fa39bd02d2e9e52"  # 如果没有设置加签，设为 None

    alerter = DingTalkAlerter(webhook_url=WEBHOOK, secret=SECRET)

    # 模拟模块三（孤立森林）吐出的异常结果
    alerter.send_anomaly_alert(
        item_name="AK-47 | Redline (Field-Tested)",
        current_price=150.50,
        obi=0.85,  # 极强的买单托盘
        sdr=0.15,  # 挂单量锐减了 15%
        signal_type="疑似盘主建仓扫货"
    )