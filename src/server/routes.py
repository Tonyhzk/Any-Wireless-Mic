"""Flask 路由 + SocketIO 事件注册"""

import io
import wave
import struct
from datetime import datetime
from pathlib import Path

import numpy as np
from flask import render_template, request, jsonify, send_file, abort, Response


def _format_file_size(size_bytes):
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    elif size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.1f} MB"
    else:
        return f"{size_bytes / (1024 * 1024 * 1024):.1f} GB"


def _format_duration(duration_seconds):
    if duration_seconds < 60:
        return f"{int(duration_seconds)}秒"
    elif duration_seconds < 3600:
        mins = int(duration_seconds // 60)
        secs = int(duration_seconds % 60)
        return f"{mins}分{secs}秒"
    else:
        hours = int(duration_seconds // 3600)
        mins = int((duration_seconds % 3600) // 60)
        secs = int(duration_seconds % 60)
        return f"{hours}时{mins}分{secs}秒"


def _get_wav_duration(filepath):
    """获取 WAV 文件时长，支持标准和 Float32 格式"""
    try:
        with wave.open(str(filepath), 'rb') as wf:
            frames = wf.getnframes()
            rate = wf.getframerate()
            return frames / rate if rate > 0 else 0
    except wave.Error:
        try:
            with open(str(filepath), 'rb') as f:
                riff = f.read(4)
                if riff != b'RIFF':
                    return 0
                f.read(4)
                wave_id = f.read(4)
                if wave_id != b'WAVE':
                    return 0
                sample_rate = 44100
                channels = 1
                bits_per_sample = 32
                data_size = 0
                while True:
                    chunk_id = f.read(4)
                    if len(chunk_id) < 4:
                        break
                    chunk_size = struct.unpack('<I', f.read(4))[0]
                    if chunk_id == b'fmt ':
                        fmt_start = f.tell()
                        f.read(2)  # audio_format
                        channels = struct.unpack('<H', f.read(2))[0]
                        sample_rate = struct.unpack('<I', f.read(4))[0]
                        f.read(4)  # byte_rate
                        f.read(2)  # block_align
                        bits_per_sample = struct.unpack('<H', f.read(2))[0]
                        remaining = chunk_size - (f.tell() - fmt_start)
                        if remaining > 0:
                            f.read(remaining)
                    elif chunk_id == b'data':
                        data_size = chunk_size
                        break
                    else:
                        f.read(chunk_size)
                if sample_rate > 0 and data_size > 0:
                    bytes_per_frame = (bits_per_sample // 8) * channels
                    total_frames = data_size // bytes_per_frame
                    return total_frames / sample_rate
        except:
            pass
    return 0


def _is_float32_wav(filepath):
    """检测 WAV 文件是否为 Float32 (IEEE) 格式"""
    try:
        with wave.open(str(filepath), 'rb'):
            return False  # 标准 wave 模块能打开，说明不是 Float32
    except wave.Error:
        try:
            with open(str(filepath), 'rb') as f:
                riff = f.read(4)
                f.read(4)
                wave_id = f.read(4)
                if riff != b'RIFF' or wave_id != b'WAVE':
                    return False
                while True:
                    chunk_id = f.read(4)
                    if len(chunk_id) < 4:
                        break
                    chunk_size = struct.unpack('<I', f.read(4))[0]
                    if chunk_id == b'fmt ':
                        audio_format = struct.unpack('<H', f.read(2))[0]
                        f.read(2)  # channels
                        f.read(4)  # sample_rate
                        f.read(4)  # byte_rate
                        f.read(2)  # block_align
                        bits = struct.unpack('<H', f.read(2))[0]
                        return audio_format == 3 and bits == 32
                    else:
                        f.read(chunk_size)
        except:
            pass
    return False


def _convert_float32_to_int16(filepath):
    """将 Float32 WAV 转换为 Int16 WAV，返回 BytesIO 对象"""
    with open(str(filepath), 'rb') as f:
        f.read(12)  # RIFF + size + WAVE
        sample_rate = 44100
        channels = 1
        raw_data = None
        while True:
            chunk_id = f.read(4)
            if len(chunk_id) < 4:
                break
            chunk_size = struct.unpack('<I', f.read(4))[0]
            if chunk_id == b'fmt ':
                f.read(2)  # audio_format
                channels = struct.unpack('<H', f.read(2))[0]
                sample_rate = struct.unpack('<I', f.read(4))[0]
                remaining = chunk_size - 8
                if remaining > 0:
                    f.read(remaining)
            elif chunk_id == b'data':
                raw_data = f.read(chunk_size)
                break
            else:
                f.read(chunk_size)

    if raw_data is None:
        raise ValueError("未找到音频数据")

    float_array = np.frombuffer(raw_data, dtype=np.float32)
    int16_array = (np.clip(float_array, -1.0, 1.0) * 32767).astype(np.int16)

    buf = io.BytesIO()
    with wave.open(buf, 'wb') as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(int16_array.tobytes())
    buf.seek(0)
    return buf


def register_routes(ctx):
    """
    注册所有 Flask 路由和 SocketIO 事件。

    ctx 是一个对象，需要提供以下属性：
        flask_app, socketio, record_dir, audio_engine,
        config, is_recording, connected_clients, mic_active_clients,
        log (日志回调), schedule_ui (在UI线程执行回调),
        on_connect, on_disconnect, on_toggle_recording,
        on_mic_status_changed, broadcast_queue
    """
    app = ctx.flask_app
    socketio = ctx.socketio

    # 设置 templates 目录为 web/
    app.template_folder = str(Path(__file__).parent.parent / 'web')

    @app.route('/')
    def index():
        return render_template('index.html')

    @app.route('/api/audio/list')
    def get_audio_list():
        try:
            audio_files = []
            wav_files = sorted(ctx.record_dir.glob("*.wav"), key=lambda x: x.stat().st_mtime, reverse=True)
            for wav_file in wav_files:
                filename = wav_file.name
                file_stat = wav_file.stat()
                mtime = datetime.fromtimestamp(file_stat.st_mtime)
                duration = _get_wav_duration(wav_file)
                audio_files.append({
                    'filename': filename,
                    'size': file_stat.st_size,
                    'size_str': _format_file_size(file_stat.st_size),
                    'mtime': mtime.strftime("%Y-%m-%d %H:%M:%S"),
                    'duration': duration,
                    'duration_str': _format_duration(duration)
                })
            return jsonify({'success': True, 'files': audio_files})
        except Exception as e:
            ctx.log(f"获取音频列表失败: {e}", "ERROR")
            return jsonify({'success': False, 'error': str(e)}), 500

    @app.route('/api/audio/play/<filename>')
    def play_audio(filename):
        try:
            if '..' in filename or '/' in filename or '\\' in filename:
                abort(400, description="Invalid filename")
            filepath = ctx.record_dir / filename
            if not filepath.exists():
                abort(404, description="File not found")
            ctx.log(f"手机端播放音频: {filename}", "INFO")
            # Float32 WAV 浏览器不支持，转换为 Int16 再返回
            if _is_float32_wav(filepath):
                ctx.log(f"检测到 Float32 格式，转换为 Int16 后返回 (size: orig={filepath.stat().st_size})", "INFO")
                buf = _convert_float32_to_int16(filepath)
                data = buf.read()
                ctx.log(f"转换完成，返回 {len(data)} bytes", "INFO")
                return Response(data, mimetype='audio/wav', headers={
                    'Content-Length': len(data),
                    'Cache-Control': 'no-cache, no-store',
                })
            return send_file(str(filepath), mimetype='audio/wav', as_attachment=False, download_name=filename)
        except Exception as e:
            ctx.log(f"播放音频失败: {e}", "ERROR")
            abort(500, description=str(e))

    @app.route('/api/audio/download/<filename>')
    def download_audio(filename):
        try:
            if '..' in filename or '/' in filename or '\\' in filename:
                abort(400, description="Invalid filename")
            filepath = ctx.record_dir / filename
            if not filepath.exists():
                abort(404, description="File not found")
            ctx.log(f"手机端下载音频: {filename}", "INFO")
            return send_file(str(filepath), mimetype='audio/wav', as_attachment=True, download_name=filename)
        except Exception as e:
            ctx.log(f"下载音频失败: {e}", "ERROR")
            abort(500, description=str(e))

    @app.route('/api/audio/delete/<filename>', methods=['DELETE'])
    def delete_audio(filename):
        try:
            if '..' in filename or '/' in filename or '\\' in filename:
                return jsonify({'success': False, 'error': 'Invalid filename'}), 400
            filepath = ctx.record_dir / filename
            if not filepath.exists():
                return jsonify({'success': False, 'error': 'File not found'}), 404

            from send2trash import send2trash
            delete_to_trash = ctx.config.get("delete_to_trash", True)
            if delete_to_trash:
                send2trash(str(filepath))
                ctx.log(f"手机端删除音频到回收站: {filename}", "WARNING")
            else:
                filepath.unlink()
                ctx.log(f"手机端永久删除音频: {filename}", "WARNING")

            ctx.schedule_ui(ctx.refresh_file_list)
            return jsonify({'success': True, 'message': f'已删除 {filename}'})
        except Exception as e:
            ctx.log(f"删除音频失败: {e}", "ERROR")
            return jsonify({'success': False, 'error': str(e)}), 500

    @app.route('/api/audio/info/<filename>')
    def get_audio_info(filename):
        try:
            if '..' in filename or '/' in filename or '\\' in filename:
                return jsonify({'success': False, 'error': 'Invalid filename'}), 400
            filepath = ctx.record_dir / filename
            if not filepath.exists():
                return jsonify({'success': False, 'error': 'File not found'}), 404
            file_stat = filepath.stat()
            with wave.open(str(filepath), 'rb') as wf:
                channels = wf.getnchannels()
                sample_width = wf.getsampwidth()
                frame_rate = wf.getframerate()
                n_frames = wf.getnframes()
                duration = n_frames / frame_rate if frame_rate > 0 else 0
            info = {
                'filename': filename,
                'size': file_stat.st_size,
                'size_str': _format_file_size(file_stat.st_size),
                'mtime': datetime.fromtimestamp(file_stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
                'duration': duration,
                'duration_str': _format_duration(duration),
                'channels': channels,
                'sample_rate': frame_rate,
                'bit_depth': sample_width * 8
            }
            return jsonify({'success': True, 'info': info})
        except Exception as e:
            ctx.log(f"获取音频信息失败: {e}", "ERROR")
            return jsonify({'success': False, 'error': str(e)}), 500

    # ==================== SocketIO 事件 ====================

    @socketio.on('audio_data')
    def handle_audio(data):
        ctx.audio_engine.write_audio(data)

    @socketio.on('connect')
    def handle_connect():
        from flask_socketio import emit
        ctx.on_connect(request.remote_addr, request.sid)
        emit('recording_status', {'is_recording': ctx.is_recording})
        emit('native_mode_status', {'enabled': ctx.audio_engine.is_float32_mode})

    @socketio.on('disconnect')
    def handle_disconnect():
        ctx.on_disconnect(request.remote_addr, request.sid)

    @socketio.on('toggle_recording')
    def handle_toggle_recording():
        import time
        ctx.log("收到手机端录制控制请求", "INFO")
        ctx.schedule_ui(ctx.on_toggle_recording)
        time.sleep(0.2)
        socketio.emit('recording_status', {'is_recording': ctx.is_recording})

    @socketio.on('request_recording_status')
    def handle_request_status():
        from flask_socketio import emit
        emit('recording_status', {'is_recording': ctx.is_recording})

    @socketio.on('set_native_mode')
    def handle_set_native_mode(data):
        enabled = data.get('enabled', False)
        ctx.audio_engine.set_float32_mode(enabled)
        ctx.log(f"手机端切换原生模式: {'开启' if enabled else '关闭'}", "INFO")

    @socketio.on('update_config')
    def handle_update_config(data):
        sample_rate = data.get('sampleRate')
        if sample_rate:
            ctx.schedule_ui(lambda: ctx.audio_engine.set_input_sample_rate(int(sample_rate)))

    @socketio.on('mic_status')
    def handle_mic_status(data):
        is_open = data.get('is_open', False)
        ctx.on_mic_status_changed(request.sid, is_open)
