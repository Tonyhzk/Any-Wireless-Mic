"""音频播放引擎 - 支持 Int16 和 Float32 WAV 文件"""

import time
import wave
import struct
import threading
from pathlib import Path

import pyaudio


class AudioPlayer:
    def __init__(self, log_callback):
        self.pa = pyaudio.PyAudio()
        self.stream = None
        self.log = log_callback
        self.is_playing = False
        self.is_paused = False
        self.current_file = None
        self.wav_file = None
        self.total_frames = 0
        self.current_frame = 0
        self.play_thread = None
        self.stop_flag = False
        self.seek_request = None

    def load_file(self, filepath):
        try:
            self.stop()
            try:
                self.wav_file = wave.open(filepath, 'rb')
                self.total_frames = self.wav_file.getnframes()
                self.current_frame = 0
                self.current_file = filepath
                self.log(f"已加载音频文件: {Path(filepath).name}", "INFO")
                return True
            except wave.Error:
                try:
                    with open(filepath, 'rb') as f:
                        riff = f.read(4)
                        f.read(4)
                        wave_id = f.read(4)
                        if riff != b'RIFF' or wave_id != b'WAVE':
                            raise ValueError("Invalid WAV")
                        fmt_found = False
                        data_found = False
                        total_frames = 0
                        while True:
                            chunk_id = f.read(4)
                            if len(chunk_id) < 4:
                                break
                            chunk_size = struct.unpack('<I', f.read(4))[0]
                            if chunk_id == b'fmt ':
                                audio_format = struct.unpack('<H', f.read(2))[0]
                                struct.unpack('<H', f.read(2))[0]  # channels
                                struct.unpack('<I', f.read(4))[0]  # rate
                                f.read(4)  # byte rate
                                f.read(2)  # align
                                bits = struct.unpack('<H', f.read(2))[0]
                                if audio_format == 3 and bits == 32:
                                    fmt_found = True
                                f.read(chunk_size - 16)
                            elif chunk_id == b'data':
                                data_found = True
                                total_frames = chunk_size // 4
                                break
                            else:
                                f.read(chunk_size)
                        if fmt_found and data_found:
                            self.wav_file = None
                            self.float32_file = filepath
                            self.total_frames = total_frames
                            self.current_frame = 0
                            self.current_file = filepath
                            self.log(f"已加载 Float32 音频文件: {Path(filepath).name}", "INFO")
                            return True
                except:
                    pass
                raise
        except Exception as e:
            self.log(f"加载音频文件失败: {e}", "ERROR")
            return False

    def play(self):
        if not self.wav_file and not hasattr(self, 'float32_file'):
            self.log("请先加载音频文件", "WARNING")
            return False
        if self.is_paused:
            self.is_paused = False
            self.is_playing = True
            self.log("继续播放", "INFO")
            return True
        self.stop_flag = False
        self.is_playing = True
        self.play_thread = threading.Thread(target=self._play_worker, daemon=True)
        self.play_thread.start()
        self.log("开始播放", "INFO")
        return True

    def _play_worker(self):
        stream = None
        try:
            chunk_size = 1024
            is_float32 = False
            f = None
            if hasattr(self, 'float32_file') and self.wav_file is None:
                is_float32 = True
                f = open(self.float32_file, 'rb')
                f.seek(44)

            # 每次播放都创建新的流
            if is_float32:
                stream = self.pa.open(
                    format=pyaudio.paFloat32, channels=1, rate=44100, output=True
                )
            else:
                stream = self.pa.open(
                    format=self.pa.get_format_from_width(self.wav_file.getsampwidth()),
                    channels=self.wav_file.getnchannels(),
                    rate=self.wav_file.getframerate(),
                    output=True
                )

            while self.is_playing and not self.stop_flag:
                if self.seek_request is not None:
                    target_frame = self.seek_request
                    self.seek_request = None
                    self.current_frame = target_frame
                    if is_float32:
                        f.seek(44 + target_frame * 4)
                    elif self.wav_file:
                        self.wav_file.setpos(target_frame)

                if self.is_paused:
                    time.sleep(0.1)
                    continue

                if is_float32:
                    data = f.read(chunk_size * 4)
                else:
                    data = self.wav_file.readframes(chunk_size)

                if not data:
                    self.is_playing = False
                    self.current_frame = 0
                    if is_float32:
                        f.seek(44)
                    else:
                        self.wav_file.rewind()
                    self.log("播放完成", "SUCCESS")
                    break

                stream.write(data)
                # 简单地增加 chunk_size，避免复杂计算导致的问题
                self.current_frame = min(self.current_frame + chunk_size, self.total_frames)

            if is_float32 and f:
                f.close()
        except Exception as e:
            self.log(f"播放错误: {e}", "ERROR")
            self.is_playing = False
        finally:
            # 清理流
            if stream:
                try:
                    stream.stop_stream()
                    stream.close()
                except:
                    pass

    def pause(self):
        if self.is_playing and not self.is_paused:
            self.is_paused = True
            self.log("已暂停", "INFO")
            return True
        return False

    def stop(self):
        self.stop_flag = True
        self.is_playing = False
        self.is_paused = False
        # 等待播放线程结束
        if self.play_thread and self.play_thread.is_alive():
            self.play_thread.join(timeout=1.0)
        if self.wav_file:
            self.wav_file.rewind()
            self.current_frame = 0
        elif hasattr(self, 'float32_file'):
            self.current_frame = 0
        self.log("已停止播放", "INFO")

    def seek(self, frame_position):
        if not (0 <= frame_position <= self.total_frames):
            return False
        if self.is_playing:
            self.seek_request = frame_position
            self.current_frame = frame_position
            return True
        else:
            self.current_frame = frame_position
            if self.wav_file:
                self.wav_file.setpos(frame_position)
            return True

    def get_progress(self):
        if self.total_frames > 0:
            return min(1.0, self.current_frame / self.total_frames)
        return 0.0

    def get_duration(self):
        if self.wav_file:
            return self.total_frames / self.wav_file.getframerate()
        elif hasattr(self, 'float32_file'):
            return self.total_frames / 44100
        return 0

    def get_current_time(self):
        if self.wav_file:
            return self.current_frame / self.wav_file.getframerate()
        elif hasattr(self, 'float32_file'):
            return self.current_frame / 44100
        return 0

    def close(self):
        self.stop()
        if self.wav_file:
            self.wav_file.close()
        self.pa.terminate()
