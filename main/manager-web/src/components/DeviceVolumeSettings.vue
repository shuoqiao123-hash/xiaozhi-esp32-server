<template>
  <el-drawer
    :visible.sync="drawerVisible"
    :before-close="handleClose"
    direction="rtl"
    size="400px"
    :modal="true"
    :show-close="false"
    custom-class="device-volume-drawer"
  >
    <div class="drawer-header" slot="title">
      <span class="drawer-title">{{ $t('device.volumeSettings') }}</span>
      <button class="drawer-close-btn" @click="handleClose">×</button>
    </div>

    <div class="drawer-content">
      <el-form label-position="top">
        <el-form-item :label="$t('device.volume')">
          <div class="slider-container">
            <el-slider
              v-model="localVolume"
              :min="0"
              :max="100"
              :step="1"
              :format-tooltip="formatTooltip"
              class="device-volume-slider"
            />
          </div>
        </el-form-item>
      </el-form>
    </div>

    <div class="drawer-footer">
      <el-button @click="handleCancel">{{ $t('button.cancel') }}</el-button>
      <el-button type="primary" :loading="saving" @click="handleSave">{{ $t('button.save') }}</el-button>
    </div>
  </el-drawer>
</template>

<script>
export default {
  name: 'DeviceVolumeSettings',
  props: {
    visible: {
      type: Boolean,
      default: false
    },
    volume: {
      type: Number,
      default: 50
    },
    saving: {
      type: Boolean,
      default: false
    }
  },
  data() {
    return {
      localVolume: 50
    };
  },
  computed: {
    drawerVisible: {
      get() {
        return this.visible;
      },
      set(val) {
        this.$emit('update:visible', val);
      }
    }
  },
  watch: {
    visible(newVal) {
      if (newVal) {
        this.localVolume = this.normalizeVolume(this.volume);
      }
    },
    volume(newVal) {
      if (this.visible) {
        this.localVolume = this.normalizeVolume(newVal);
      }
    }
  },
  methods: {
    normalizeVolume(value) {
      const numberValue = Number(value);
      if (Number.isNaN(numberValue)) {
        return 50;
      }
      return Math.min(100, Math.max(0, Math.round(numberValue)));
    },
    handleClose() {
      this.$emit('update:visible', false);
    },
    handleCancel() {
      this.handleClose();
    },
    handleSave() {
      this.$emit('save', this.normalizeVolume(this.localVolume));
    },
    formatTooltip(val) {
      return `${val}%`;
    }
  }
};
</script>

<style scoped>
.drawer-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 20px 24px;
  border-bottom: 1px solid #e8f0ff;
}

.drawer-title {
  font-size: 18px;
  font-weight: 600;
  color: #3d4566;
}

.drawer-close-btn {
  width: 32px;
  height: 32px;
  border-radius: 50%;
  border: 2px solid #cfcfcf;
  background: none;
  font-size: 28px;
  font-weight: lighter;
  color: #cfcfcf;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 0;
  outline: none;
  transition: all 0.3s;
}

.drawer-close-btn:hover {
  color: #409eff;
  border-color: #409eff;
}

.drawer-content {
  padding: 24px;
  flex: 1;
  overflow-y: auto;
}

.slider-container {
  width: 100%;
}

.device-volume-slider {
  width: 100%;
}

.device-volume-slider ::v-deep .el-slider__input {
  width: 80px;
}

.device-volume-slider ::v-deep .el-input__inner {
  text-align: center;
  padding: 0 8px;
}

.drawer-footer {
  padding: 16px 24px;
  border-top: 1px solid #e8f0ff;
  display: flex;
  justify-content: center;
  gap: 12px;
}

.drawer-footer .el-button {
  min-width: 80px;
}

::v-deep .el-form-item__label {
  font-size: 14px !important;
  color: #3d4566 !important;
  font-weight: 500;
  padding-bottom: 8px;
}

::v-deep .el-form-item {
  margin-bottom: 24px;
}
</style>

<style>
.device-volume-drawer .el-drawer__header {
  margin-bottom: 0;
  padding: 0;
}

.device-volume-drawer .el-drawer__body {
  display: flex;
  flex-direction: column;
  padding: 0;
}
</style>
