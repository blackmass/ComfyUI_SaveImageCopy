<img width="308" height="553" alt="Image" src="https://github.com/user-attachments/assets/66080fa6-be15-4615-9900-c9baeda8f9d2" />

# ComfyUI_SaveImageCopy
I have added features to the existing official SaveImage node, including viewing the image in full screen, opening the folder, and creating backups.
## Functions
- By default, the node saves images to the 'save_prefix' path.
- Clicking the diskette button saves the image to the 'backup_prefix' path.
- If the path is empty or ends with a '/', the file is saved using the name 'ComfyUI' (without an appended underscore). (e.g., 'moss' -> 'moss_0001.png', 'moss/' -> 'moss/ComfyUI_0001.png')
- Clicking the folder button opens the 'save_prefix' directory.
- Clicking on the preview image allows you to view it in full screen.

# Created using Claude; I do not claim copyright.
