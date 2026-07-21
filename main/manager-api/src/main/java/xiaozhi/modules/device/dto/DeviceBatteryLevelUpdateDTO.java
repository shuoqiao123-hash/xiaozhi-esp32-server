package xiaozhi.modules.device.dto;

import jakarta.validation.constraints.Max;
import jakarta.validation.constraints.Min;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;
import lombok.Data;

import java.io.Serializable;

@Data
public class DeviceBatteryLevelUpdateDTO implements Serializable {
    @NotBlank
    private String macAddress;

    @NotNull
    @Min(0)
    @Max(100)
    private Integer batteryLevel;

    private static final long serialVersionUID = 1L;
}
