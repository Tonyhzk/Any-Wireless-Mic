"""音频处理引擎 - 支持 Int16 和 Float32 双格式，双输出设备（监听+虚拟麦克风）"""

import wave
import threading
import numpy as np
import pyaudio

from config import CHUNK, FORMAT, FORMAT_FLOAT32, CHANNELS, RATE


class AudioEngine:
    def __init__(self, log_callback):
        self.pa = pyaudio.PyAudio()
        self.monitor_stream = None
        self.virtual_mic_stream = None
        self.lock = threading.Lock()
        self.log = log_callback
        self.is_recording = False
        self.record_frames = []

        # 双设备索引
        self.monitor_device_index = None
        self.virtual_mic_device_index = None

        # 播放控制
        self.enable_monitor_playback = True
        self.enable_virtual_mic_output = True

        # 音频格式状态
        self.current_format = FORMAT
        self.recording_format = FORMAT
        self.recording_sample_rate = RATE
        self.is_float32_mode = False

        # 采样率控制
        self.target_sample_rate = RATE
        self.input_sample_rate = RATE
        self.resample_state = None

        # 实时波形更新回调
        self.waveform_callback = None

    def set_input_sample_rate(self, rate):
        if rate != self.input_sample_rate:
            self.input_sample_rate = rate
            self.resample_state = None
            self.log(f"输入采样率调整为: {rate} Hz", "INFO")

    def set_float32_mode(self, enabled):
        if self.is_float32_mode == enabled:
            return
        self.is_float32_mode = enabled
        if enabled:
            self.recording_format = FORMAT_FLOAT32
            self.input_sample_rate = RATE  # 原生模式固定 44100Hz
            self.log("已切换到 Float32 高音质模式 (录制: Float32, 播放: Int16)", "SUCCESS")
        else:
            self.recording_format = FORMAT
            self.log("已切换到 Int16 标准模式", "INFO")

    def get_output_devices(self):
        devices = []
        try:
            info = self.pa.get_host_api_info_by_index(0)
            num_devices = info.get('deviceCount')
            for i in range(num_devices):
                device_info = self.pa.get_device_info_by_host_api_device_index(0, i)
                if device_info.get('maxOutputChannels') > 0:
                    devices.append({
                        'index': i,
                        'name': device_info.get('name')
                    })
        except Exception as e:
            self.log(f"获取设备列表失败: {e}", "ERROR")
        return devices

    def start_monitor_stream(self, device_index):
        self.stop_monitor_stream()
        with self.lock:
            try:
                self.monitor_stream = self.pa.open(
                    format=FORMAT, channels=CHANNELS, rate=RATE,
                    output=True, output_device_index=device_index,
                    frames_per_buffer=CHUNK
                )
                self.monitor_device_index = device_index
                self.log(f"成功开启监听流 (设备ID: {device_index})", "INFO")
                return True
            except Exception as e:
                self.log(f"开启监听流失败: {e}", "ERROR")
                return False

    def stop_monitor_stream(self):
        with self.lock:
            if self.monitor_stream:
                try:
                    self.monitor_stream.stop_stream()
                    self.monitor_stream.close()
                except:
                    pass
                self.monitor_stream = None
                self.log("监听流已关闭", "INFO")

    def start_virtual_mic_stream(self, device_index):
        self.stop_virtual_mic_stream()
        with self.lock:
            try:
                self.virtual_mic_stream = self.pa.open(
                    format=FORMAT, channels=CHANNELS, rate=RATE,
                    output=True, output_device_index=device_index,
                    frames_per_buffer=CHUNK
                )
                self.virtual_mic_device_index = device_index
                self.log(f"成功开启虚拟麦克风流 (设备ID: {device_index})", "INFO")
                return True
            except Exception as e:
                self.log(f"开启虚拟麦克风流失败: {e}", "ERROR")
                return False

    def stop_virtual_mic_stream(self):
        with self.lock:
            if self.virtual_mic_stream:
                try:
                    self.virtual_mic_stream.stop_stream()
                    self.virtual_mic_stream.close()
                except:
                    pass
                self.virtual_mic_stream = None
                self.log("虚拟麦克风流已关闭", "INFO")

    def stop_all_streams(self):
        self.stop_monitor_stream()
        self.stop_virtual_mic_stream()

    def write_audio(self, data):
        """写入音频数据 - 录制/监听/虚拟麦克风/波形 四条链路并行"""
        if not isinstance(data, (bytes, bytearray)):
            try:
                data = bytes(data)
            except Exception:
                return
        if len(data) == 0:
            return

        # 链路1: 录制（保存原始数据）
        if self.is_recording:
            self.record_frames.append(data)

        # 链路2 & 3: 播放（始终转为 Int16）
        playback_data = None

        if self.is_float32_mode:
            if len(data) % 4 == 0 and len(data) >= 4:
                try:
                    float_array = np.frombuffer(data, dtype=np.float32)
                    int16_array = (np.clip(float_array, -1.0, 1.0) * 32767).astype(np.int16)
                    playback_data = int16_array.tobytes()
                except Exception as e:
                    sample_count = len(data) // 4
                    playback_data = np.zeros(sample_count, dtype=np.int16).tobytes()
                    self.log(f"Float32→Int16 转换失败，输出静音: {e}", "WARNING")
            else:
                sample_count = max(1, len(data) // 4)
                playback_data = np.zeros(sample_count, dtype=np.int16).tobytes()
        else:
            playback_data = data
            if self.input_sample_rate != self.target_sample_rate:
                try:
                    int16_array = np.frombuffer(data, dtype=np.int16)
                    num_samples = len(int16_array)
                    if num_samples > 0:
                        new_num_samples = int(num_samples * self.target_sample_rate / self.input_sample_rate)
                        x_old = np.linspace(0, 1, num_samples)
                        x_new = np.linspace(0, 1, new_num_samples)
                        resampled = np.interp(x_new, x_old, int16_array)
                        playback_data = np.clip(resampled, -32768, 32767).astype(np.int16).tobytes()
                except Exception:
                    pass

        if playback_data is None or len(playback_data) == 0:
            return

        # 写入监听流
        if self.enable_monitor_playback:
            with self.lock:
                if self.monitor_stream:
                    try:
                        self.monitor_stream.write(playback_data)
                    except Exception:
                        pass

        # 写入虚拟麦克风流
        if self.enable_virtual_mic_output:
            with self.lock:
                if self.virtual_mic_stream:
                    try:
                        self.virtual_mic_stream.write(playback_data)
                    except Exception:
                        pass

        # 更新实时波形
        if self.waveform_callback and playback_data:
            try:
                self.waveform_callback(playback_data)
            except Exception:
                pass

    def start_recording(self):
        self.record_frames = []
        self.is_recording = True
        self.recording_sample_rate = self.input_sample_rate
        self.log(f"开始录制音频 (采样率: {self.recording_sample_rate} Hz)...", "INFO")

    def stop_recording(self):
        self.is_recording = False
        format_name = "Float32 (32-bit)" if self.recording_format == FORMAT_FLOAT32 else "Int16 (16-bit)"
        self.log(f"停止录制，捕获到 {len(self.record_frames)} 个数据块 (格式: {format_name}, 采样率: {self.recording_sample_rate} Hz)", "INFO")
        return self.record_frames, self.recording_format, self.recording_sample_rate

    def save_wav(self, frames, filepath, data_format=None, sample_rate=None):
        try:
            if len(frames) == 0:
                self.log("没有音频数据可保存", "WARNING")
                return False
            if data_format is None:
                data_format = self.recording_format
            if sample_rate is None:
                sample_rate = self.recording_sample_rate

            audio_data = b''.join(frames)

            if data_format == FORMAT_FLOAT32:
                return self._save_float32_wav(audio_data, filepath, sample_rate)
            else:
                wf = wave.open(filepath, 'wb')
                wf.setnchannels(CHANNELS)
                wf.setsampwidth(self.pa.get_sample_size(FORMAT))
                wf.setframerate(sample_rate)
                wf.writeframes(audio_data)
                wf.close()
                self.log(f"音频已保存至: {filepath} (16-bit, {sample_rate}Hz)", "SUCCESS")
                return True
        except Exception as e:
            self.log(f"保存WAV失败: {e}", "ERROR")
            return False

    def _save_float32_wav(self, audio_data, filepath, sample_rate=None):
        import struct
        try:
            if sample_rate is None:
                sample_rate = RATE
            float_array = np.frombuffer(audio_data, dtype=np.float32)
            num_samples = len(float_array)
            num_channels = CHANNELS
            bits_per_sample = 32
            byte_rate = sample_rate * num_channels * bits_per_sample // 8
            block_align = num_channels * bits_per_sample // 8
            data_size = num_samples * block_align

            with open(filepath, 'wb') as f:
                f.write(b'RIFF')
                f.write(struct.pack('<I', 36 + data_size))
                f.write(b'WAVE')
                f.write(b'fmt ')
                f.write(struct.pack('<I', 16))
                f.write(struct.pack('<H', 3))  # IEEE Float
                f.write(struct.pack('<H', num_channels))
                f.write(struct.pack('<I', sample_rate))
                f.write(struct.pack('<I', byte_rate))
                f.write(struct.pack('<H', block_align))
                f.write(struct.pack('<H', bits_per_sample))
                f.write(b'data')
                f.write(struct.pack('<I', data_size))
                f.write(float_array.tobytes())

            self.log(f"音频已保存至: {filepath} (32-bit Float 高音质)", "SUCCESS")
            return True
        except Exception as e:
            self.log(f"保存 Float32 WAV 失败: {e}", "ERROR")
            self.log("尝试降级保存为 16-bit WAV...", "WARNING")
            try:
                float_array = np.frombuffer(audio_data, dtype=np.float32)
                int16_array = (np.clip(float_array, -1.0, 1.0) * 32767).astype(np.int16)
                wf = wave.open(filepath, 'wb')
                wf.setnchannels(CHANNELS)
                wf.setsampwidth(2)
                wf.setframerate(sample_rate)
                wf.writeframes(int16_array.tobytes())
                wf.close()
                self.log(f"音频已降级保存至: {filepath} (16-bit, {sample_rate}Hz)", "SUCCESS")
                return True
            except Exception as e2:
                self.log(f"降级保存也失败: {e2}", "ERROR")
                return False

    def close(self):
        self.stop_all_streams()
        self.pa.terminate()
