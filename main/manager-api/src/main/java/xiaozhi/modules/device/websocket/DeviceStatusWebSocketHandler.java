package xiaozhi.modules.device.websocket;

import java.io.IOException;
import java.text.SimpleDateFormat;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;

import org.apache.commons.lang3.StringUtils;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;
import org.springframework.web.socket.CloseStatus;
import org.springframework.web.socket.TextMessage;
import org.springframework.web.socket.WebSocketSession;
import org.springframework.web.socket.handler.TextWebSocketHandler;

import cn.hutool.json.JSONObject;
import cn.hutool.json.JSONUtil;
import lombok.AllArgsConstructor;
import lombok.Data;
import lombok.extern.slf4j.Slf4j;
import xiaozhi.common.exception.RenException;
import xiaozhi.modules.device.service.DeviceService;
import xiaozhi.modules.device.vo.DeviceStatusVO;
import xiaozhi.modules.security.service.ShiroService;
import xiaozhi.modules.security.entity.SysUserTokenEntity;
import xiaozhi.modules.sys.entity.SysUserEntity;

@Slf4j
@Component
@AllArgsConstructor
public class DeviceStatusWebSocketHandler extends TextWebSocketHandler {

    private final DeviceService deviceService;
    private final ShiroService shiroService;
    private final Map<String, Subscription> subscriptions = new ConcurrentHashMap<>();

    @Override
    public void afterConnectionEstablished(WebSocketSession session) throws Exception {
        String token = extractToken(session);
        if (StringUtils.isBlank(token)) {
            sendError(session, "未提供token");
            session.close();
            return;
        }

        SysUserTokenEntity tokenEntity = shiroService.getByToken(token);
        if (tokenEntity == null || tokenEntity.getExpireDate() == null
                || tokenEntity.getExpireDate().getTime() < System.currentTimeMillis()) {
            sendError(session, "token无效或已过期");
            session.close();
            return;
        }

        SysUserEntity userEntity = shiroService.getUser(tokenEntity.getUserId());
        if (userEntity == null || userEntity.getId() == null) {
            sendError(session, "用户不存在");
            session.close();
            return;
        }

        subscriptions.put(session.getId(), new Subscription(session, userEntity.getId(), userEntity.getSuperAdmin() != null && userEntity.getSuperAdmin() == 1));
    }

    @Override
    protected void handleTextMessage(WebSocketSession session, TextMessage message) throws Exception {
        Subscription subscription = subscriptions.get(session.getId());
        if (subscription == null) {
            sendError(session, "连接未初始化");
            session.close();
            return;
        }

        JSONObject payload = JSONUtil.parseObj(message.getPayload());
        String type = payload.getStr("type");
        if (!"subscribe_device".equals(type)) {
            sendError(session, "不支持的消息类型");
            return;
        }

        String macAddress = payload.getStr("macAddress");
        if (StringUtils.isBlank(macAddress)) {
            sendError(session, "macAddress不能为空");
            return;
        }

        subscription.setMacAddress(macAddress);
        pushDeviceStatus(subscription);

        JSONObject result = new JSONObject();
        result.set("type", "subscribed");
        result.set("macAddress", macAddress);
        sendJson(session, result);
    }

    @Override
    public void afterConnectionClosed(WebSocketSession session, CloseStatus status) throws Exception {
        subscriptions.remove(session.getId());
    }

    @Override
    public void handleTransportError(WebSocketSession session, Throwable exception) throws Exception {
        subscriptions.remove(session.getId());
        if (session.isOpen()) {
            session.close();
        }
    }

    @Scheduled(fixedDelay = 5000)
    public void pushSubscribedDeviceStatus() {
        subscriptions.values().forEach(subscription -> {
            if (!subscription.hasMacAddress()) {
                return;
            }
            pushDeviceStatus(subscription);
        });
    }

    private void pushDeviceStatus(Subscription subscription) {
        try {
            if (!subscription.getSession().isOpen()) {
                subscriptions.remove(subscription.getSession().getId());
                return;
            }
            DeviceStatusVO status = deviceService.getDeviceStatusByMacAddress(
                    subscription.getMacAddress(),
                    subscription.getUserId(),
                    subscription.isSuperAdmin());
            JSONObject result = new JSONObject();
            result.set("type", "device_status");
            result.set("data", buildDeviceStatusData(status));
            sendJson(subscription.getSession(), result);
        } catch (RenException e) {
            sendError(subscription.getSession(), e.getMessage());
        } catch (Exception e) {
            log.warn("推送设备状态失败 sessionId={}: {}", subscription.getSession().getId(), e.getMessage());
            sendError(subscription.getSession(), "推送设备状态失败");
        }
    }

    private JSONObject buildDeviceStatusData(DeviceStatusVO status) {
        JSONObject data = new JSONObject();
        data.set("macAddress", status.getMacAddress());
        data.set("online", status.getOnline());
        data.set("batteryLevel", status.getBatteryLevel());
        data.set("lastConnectedAt", status.getLastConnectedAt() == null
                ? null
                : new SimpleDateFormat("yyyy-MM-dd HH:mm:ss").format(status.getLastConnectedAt()));
        return data;
    }

    private String extractToken(WebSocketSession session) {
        String query = session.getUri() != null ? session.getUri().getQuery() : null;
        if (StringUtils.isBlank(query)) {
            return null;
        }
        for (String pair : query.split("&")) {
            String[] parts = pair.split("=", 2);
            if (parts.length == 2 && "token".equals(parts[0])) {
                return parts[1];
            }
        }
        return null;
    }

    private void sendError(WebSocketSession session, String message) {
        try {
            if (!session.isOpen()) {
                return;
            }
            JSONObject result = new JSONObject();
            result.set("type", "error");
            result.set("message", message);
            sendJson(session, result);
        } catch (Exception e) {
            log.warn("发送WebSocket错误消息失败 sessionId={}: {}", session.getId(), e.getMessage());
        }
    }

    private void sendJson(WebSocketSession session, JSONObject payload) throws IOException {
        synchronized (session) {
            if (session.isOpen()) {
                session.sendMessage(new TextMessage(payload.toString()));
            }
        }
    }

    @Data
    private static class Subscription {
        private final WebSocketSession session;
        private final Long userId;
        private final boolean superAdmin;
        private String macAddress;

        boolean hasMacAddress() {
            return StringUtils.isNotBlank(macAddress);
        }
    }
}
