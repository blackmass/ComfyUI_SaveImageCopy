# ComfyUI_SaveImageCopy
#
# 기본 'Save Image' 노드를 상속한 커스텀 노드.
#  - 저장 동작/메타데이터/출력 규격은 기본 노드와 동일
#  - 파일명 형식만 변경:  prefix_00001_.png  ->  prefix_00001.png
#  - 💾 버튼: 직전에 저장된 이미지를 지정한 폴더로 복사
#  - 📁 버튼: 직전에 저장된 이미지를 선택한 상태로 OS 탐색기 열기

import os
import re
import sys
import json
import shutil
import subprocess

import numpy as np
from PIL import Image
from PIL.PngImagePlugin import PngInfo

import folder_paths
from comfy.cli_args import args
from nodes import SaveImage

# 노드 ID별 마지막 저장 파일 경로 (현재 세션 한정).
# 서버 라우트는 이 딕셔너리에 있는 경로만 다룬다 -> 프론트가 임의 파일을 지정할 수 없음.
_LAST_SAVED = {}


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
        hidden = dict(base.get("hidden", {}))
        hidden["unique_id"] = "UNIQUE_ID"
        base["hidden"] = hidden
        return base

    CATEGORY = "image"
    DESCRIPTION = "Save Image + 💾 copy last image to a folder / 📁 reveal in file manager. No trailing '_' in filenames."

    def save_images(self, images, filename_prefix="ComfyUI", prompt=None,
                    extra_pnginfo=None, unique_id=None):
        filename_prefix += self.prefix_append
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
def _resolve_target(text: str) -> str:
    text = os.path.expanduser(str(text or "").strip())
    if text and not os.path.isabs(text):
        text = os.path.join(folder_paths.get_output_directory(), text)
    return os.path.normpath(text) if text else ""


def _copy_last(node_id, target_dir):
    path = _LAST_SAVED.get(str(node_id))
    if not path:
        return False, "저장된 이미지가 없습니다. 먼저 워크플로우를 실행하세요."
    if not os.path.isfile(path):
        return False, f"파일이 더 이상 존재하지 않습니다: {path}"
    target = _resolve_target(target_dir)
    if not target:
        return False, "경로 필드에 저장할 폴더를 입력하세요."
    try:
        os.makedirs(target, exist_ok=True)
        dst = os.path.join(target, os.path.basename(path))
        shutil.copy2(path, dst)       # 같은 이름이 있으면 덮어씀
    except Exception as e:
        return False, f"복사 실패: {e}"
    return True, f"복사 완료: {os.path.basename(path)} -> {target}"


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
            ok, msg = _copy_last(data.get("node_id", ""), data.get("target_dir", ""))
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
