import os

rootDir = '.'
for dirName, subdirList, fileList in os.walk(rootDir):
    if '.git' in dirName or '__pycache__' in dirName or '.venv' in dirName:
        continue
    for fname in fileList:
        path = os.path.join(dirName, fname)
        if not path.endswith('.py'): continue
        try:
            with open(path, 'rb') as f:
                b = f.read(1)
                if b == b'\xff':
                    print(f"FOUND 0xff in {path}")
                try:
                    # check decoding
                     with open(path, 'r', encoding='utf-8') as f2:
                         f2.read()
                except UnicodeDecodeError as e:
                    print(f"Unicode decode error in {path}: {e}")
        except Exception as e:
            print(f"Error reading {path}: {e}")
