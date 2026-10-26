# ComfyUI_SaveImageCopy
#
# 기본 'Save Image' 노드를 상속한 커스텀 노드.
#  - 저장 동작/메타데이터/출력 규격은 기본 노드와 동일
#  - 파일명 형식만 변경:  prefix_00001_.png  ->  prefix_00001.png
#  - save_prefix  : 기본 저장 경로+접두어 (기존 filename_prefix)
#  - backup_prefix: 백업 경로+접두어 (프론트 위젯, 기존 path)
#  - 💾 버튼: 직전에 저장된 이미지를 backup_prefix 기준으로 새 번호를 매겨 복사 (덮어쓰지 않음)
#  - %date:yyyy-MM-dd% 날짜 토큰은 서버에서도 직접 치환
#  - 📁 버튼: 직전에 저장된 이미지를 선택한 상태로 OS 탐색기 열기

import os
import re
import sys
import json
import shutil
import subprocess
from datetime import datetime

import numpy as np
from PIL import Image
from PIL.PngImagePlugin import PngInfo

import folder_paths
from comfy.cli_args import args
from nodes import SaveImage

# 노드 ID별 마지막 저장 파일 경로 (현재 세션 한정).
# 서버 라우트는 이 딕셔너리에 있는 경로만 다룬다 -> 프론트가 임의 파일을 지정할 수 없음.
_LAST_SAVED = {}

DEFAULT_NAME = "ComfyUI"   # 경로가 '/'로 끝날 때 쓰는 기본 파일명 접두어


_DATE_TOKEN = re.compile(r"yyyy|yy|MM|M|dd|d|hh|h|mm|m|ss|s")
_DATE_PATTERN = re.compile(r"%date:([^%]*)%")


def _apply_replacements(text) -> str:
    """%date:yyyy-MM-dd% 같은 날짜 토큰을 치환한다 (MM=월, mm=분).
    프론트가 이미 치환했다면 토큰이 없으므로 아무 일도 하지 않는다."""
    now = datetime.now()
    table = {
        "yyyy": f"{now.year:04}", "yy": f"{now.year % 100:02}",
        "MM": f"{now.month:02}", "M": str(now.month),
        "dd": f"{now.day:02}", "d": str(now.day),
        "hh": f"{now.hour:02}", "h": str(now.hour),
        "mm": f"{now.minute:02}", "m": str(now.minute),
        "ss": f"{now.second:02}", "s": str(now.second),
    }
    return _DATE_PATTERN.sub(
        lambda m: _DATE_TOKEN.sub(lambda t: table[t.group(0)], m.group(1)),
        str(text or ""))


def _with_default_name(text) -> str:
    """접두어 처리 규칙 (save_prefix / backup_prefix 공통)
    - 끝이 '/' 또는 '\\' 이면: 그 폴더 안에 기본 이름 'ComfyUI'로 넘버링
    - 끝에 구분자가 없으면: 마지막 부분이 파일명 접두어"""
    text = _apply_replacements(text).strip()
    if text and text[-1] in ("/", "\\"):
        text += DEFAULT_NAME
    return text


def _open_in_file_manager(path: str) -> None:
    path = os.path.normpath(path)
    if sys.platform == "win32":
        # explorer는 성공해도 종료코드 1을 돌려주는 경우가 많다 (오류 아님)
        subprocess.Popen(["explorer", "/select,", path])
    elif sys.platform == "darwin":
        subprocess.Popen(["open", "-R", path])
    else:
        subprocess.Popen(["xdg-open", os.path.dirname(path)])


class SaveImageCopy(SaveImage):

    @classmethod
    def INPUT_TYPES(cls):
        base = super().INPUT_TYPES()
        # filename_prefix -> save_prefix (순서 유지)
        req = base.get("required", {})
        base["required"] = {("save_prefix" if k == "filename_prefix" else k): v
                            for k, v in req.items()}
        hidden = dict(base.get("hidden", {}))
        hidden["unique_id"] = "UNIQUE_ID"
        base["hidden"] = hidden
        return base

    CATEGORY = "image"
    DESCRIPTION = "Save Image + 💾 numbered backup copy (backup_prefix) / 📁 reveal in file manager. No trailing '_' in filenames."

    def save_images(self, images, save_prefix="ComfyUI", prompt=None,
                    extra_pnginfo=None, unique_id=None):
        filename_prefix = _with_default_name(save_prefix) + self.prefix_append
        full_output_folder, filename, _counter, subfolder, filename_prefix = \
            folder_paths.get_save_image_path(
                filename_prefix, self.output_dir,
                images[0].shape[1], images[0].shape[0])

        results = []
        last_path = None

        for batch_number, image in enumerate(images):
            arr = 255.0 * image.cpu().numpy()
            img = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8))

            metadata = None
            if not args.disable_metadata:
                metadata = PngInfo()
                if prompt is not None:
                    metadata.add_text("prompt", json.dumps(prompt))
                if extra_pnginfo is not None:
                    for k in extra_pnginfo:
                        metadata.add_text(k, json.dumps(extra_pnginfo[k]))

            base = filename.replace("%batch_num%", str(batch_number))

            # 기본 카운터 계산은 파일명 끝의 '_'에 의존하므로 직접 계산한다.
            # 기본 노드가 만든 'name_00001_.png'도 함께 인식해서 번호가 이어진다.
            pattern = re.compile(re.escape(base) + r"_(\d+)_?\.png$", re.IGNORECASE)
            max_counter = 0
            for existing in os.listdir(full_output_folder):
                m = pattern.match(existing)
                if m:
                    max_counter = max(max_counter, int(m.group(1)))

            counter = max_counter + 1
            while True:
                file = f"{base}_{counter:05}.png"          # <- 끝의 '_' 없음
                full_path = os.path.join(full_output_folder, file)
                if not os.path.exists(full_path):
                    break
                counter += 1

            img.save(full_path, pnginfo=metadata, compress_level=self.compress_level)
            results.append({"filename": file, "subfolder": subfolder, "type": self.type})
            last_path = os.path.abspath(full_path)

        if unique_id is not None and last_path:
            _LAST_SAVED[str(unique_id)] = last_path   # 배치면 마지막 이미지

        # sic_key: 프론트가 서버에 요청할 때 쓸 노드 키 (서브그래프 ID 대응)
        return {"ui": {"images": results, "sic_key": [str(unique_id)]}}


# ----------------------------------------------------------------------
# 서버 라우트
# ----------------------------------------------------------------------
def _resolve_backup_target(text):
    """backup_prefix -> (폴더, 접두어). 상대경로는 output 폴더 기준.
    'saved/moss/' -> 폴더 saved/moss, 접두어 ComfyUI
    'saved/moss'  -> 폴더 saved,      접두어 moss"""
    text = _with_default_name(text)
    if not text:
        return "", ""
    text = os.path.expanduser(text)
    if not os.path.isabs(text):
        text = os.path.join(folder_paths.get_output_directory(), text)
    folder, prefix = os.path.split(os.path.normpath(text))
    return folder, (prefix or DEFAULT_NAME)


def _copy_exclusive(src, dst):
    """dst가 이미 있으면 FileExistsError (존재 확인과 생성이 한 번에 이뤄져 덮어쓰기 불가)."""
    with open(src, "rb") as fsrc, open(dst, "xb") as fdst:
        shutil.copyfileobj(fsrc, fdst)
    try:
        shutil.copystat(src, dst)
    except Exception:
        pass


def _copy_last(node_id, backup_prefix):
    path = _LAST_SAVED.get(str(node_id))
    if not path:
        return False, "저장된 이미지가 없습니다. 먼저 워크플로우를 실행하세요."
    if not os.path.isfile(path):
        return False, f"파일이 더 이상 존재하지 않습니다: {path}"
    folder, prefix = _resolve_backup_target(backup_prefix)
    if not folder:
        return False, "backup_prefix 필드에 백업 경로를 입력하세요."

    ext = os.path.splitext(path)[1] or ".png"
    pattern = re.compile(re.escape(prefix) + r"_(\d+)_?" + re.escape(ext) + r"$", re.IGNORECASE)
    try:
        os.makedirs(folder, exist_ok=True)
        max_counter = 0
        for existing in os.listdir(folder):
            m = pattern.match(existing)
            if m:
                max_counter = max(max_counter, int(m.group(1)))
        counter = max_counter + 1
        while True:
            dst = os.path.join(folder, f"{prefix}_{counter:05}{ext}")
            try:
                _copy_exclusive(path, dst)
                break
            except FileExistsError:
                counter += 1
    except Exception as e:
        return False, f"백업 실패: {e}"
    return True, f"백업 완료: {os.path.basename(path)} -> {dst}"


try:
    from server import PromptServer
    from aiohttp import web as _web

    if getattr(PromptServer.instance, "_sic_routes_registered", False):
        print("[SaveImageCopy] 라우트가 이미 등록되어 있습니다 (중복 폴더 확인). 등록을 건너뜁니다.")
    else:
        PromptServer.instance._sic_routes_registered = True

        @PromptServer.instance.routes.post("/saveimagecopy/copy_last")
        async def _sic_copy_last(request):
            try:
                data = await request.json()
            except Exception:
                data = {}
            ok, msg = _copy_last(data.get("node_id", ""),
                                data.get("backup_prefix", data.get("target_dir", "")))
            return _web.json_response({"ok": ok, "message" if ok else "error": msg})

        @PromptServer.instance.routes.post("/saveimagecopy/open_folder")
        async def _sic_open_folder(request):
            try:
                data = await request.json()
            except Exception:
                data = {}
            path = _LAST_SAVED.get(str(data.get("node_id", "")))
            if not path:
                return _web.json_response(
                    {"ok": False, "error": "저장된 이미지가 없습니다. 먼저 워크플로우를 실행하세요."})
            if not os.path.isfile(path):
                return _web.json_response(
                    {"ok": False, "error": f"파일이 더 이상 존재하지 않습니다: {path}"})
            try:
                _open_in_file_manager(path)
                return _web.json_response({"ok": True})
            except Exception as e:
                return _web.json_response({"ok": False, "error": str(e)})

except Exception as _e:
    print(f"[SaveImageCopy] 라우트 등록 실패: {_e}")


NODE_CLASS_MAPPINGS = {"SaveImageCopy": SaveImageCopy}
NODE_DISPLAY_NAME_MAPPINGS = {"SaveImageCopy": "Save Image (Copy)"}
