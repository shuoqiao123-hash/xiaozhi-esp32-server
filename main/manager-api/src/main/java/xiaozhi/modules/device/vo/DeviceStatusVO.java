package xiaozhi.modules.device.vo;

import java.util.Date;

import io.swagger.v3.oas.annotations.media.Schema;
import lombok.Data;

@Data
@Schema(description = "设备状态")
public class DeviceStatusVO {
    @Schema(description = "MAC地址")
    private String macAddress;

    @Schema(description = "是否在线")
    private Boolean online;

    @Schema(description = "设备电量")
    private Integer batteryLevel;

    @Schema(description = "最后连接时间")
    private Date lastConnectedAt;
}
