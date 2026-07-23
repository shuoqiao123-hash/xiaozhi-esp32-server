package xiaozhi.modules.device.websocket;

import org.springframework.context.annotation.Configuration;
import org.springframework.web.socket.config.annotation.EnableWebSocket;
import org.springframework.web.socket.config.annotation.WebSocketConfigurer;
import org.springframework.web.socket.config.annotation.WebSocketHandlerRegistry;

import lombok.AllArgsConstructor;

@Configuration
@EnableWebSocket
@AllArgsConstructor
public class DeviceStatusWebSocketConfig implements WebSocketConfigurer {

    private final DeviceStatusWebSocketHandler deviceStatusWebSocketHandler;

    @Override
    public void registerWebSocketHandlers(WebSocketHandlerRegistry registry) {
        registry.addHandler(deviceStatusWebSocketHandler, "/ws/device-status")
                .setAllowedOriginPatterns("*");
    }
}
