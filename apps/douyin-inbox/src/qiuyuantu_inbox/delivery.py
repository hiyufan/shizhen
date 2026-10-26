"""区分平台接收消息与允许接收方查看；回执仅导出限制标记和状态码。"""

DELIVERY_JS = r"""
const deliveryStatus = raw => {
  const ext = raw.ext || {};
  const keys = ['im_callback_status_code', 's:send_response_check_code', 's:send_response_status'];
  const values = keys.map(key => ext[key]).filter(value => value != null && value !== '');
  const code = values.map(Number).find(value => Number.isFinite(value) && value !== 0) || 0;
  const senderOnly = !!raw.sender && String(ext['s:visible'] || '') === String(raw.sender);
  return {delivery_restricted: senderOnly || values.some(value => Number(value) !== 0), delivery_code: code};
};
"""


class DeliveryRestricted(RuntimeError):
    def __init__(self, row: dict):
        self.server_id = str(row.get("id") or "")
        self.code = int(row.get("delivery_code") or 0)
        super().__init__("platform_delivery_restricted")
