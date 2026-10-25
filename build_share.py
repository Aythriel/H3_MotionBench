"""Build a code-only release using an explicit file allowlist."""
from pathlib import Path
import argparse
import hashlib
import zipfile

FILES = ['server.py','storage.py','assembly.py','conditioning.py','dependencies.py','setup_api.py',
         'index.html','app.js','style.css','assemble.html','assemble.js','assemble.css',
         'settings.html','settings.js','settings.css','Start-H3Dashboard.ps1','Start-MotionBench.cmd',
         'requirements.txt','README.md','THIRD_PARTY_NOTICES.md','build_share.py','Build-SharePackage.ps1']

def build(root, output):
    files = list(FILES)
    files += ['installers/'+name for name in ['Bootstrap-Runtime.ps1','bootstrap_pip.py','install.py','patches.py',
                                             'dependencies.json','dashboard-requirements.lock','gguf-requirements.lock']]
    files += [str(p.relative_to(root)).replace('\\','/') for p in (root/'installers').glob('Install-*.ps1')]
    for name in files:
        if not (root/name).is_file():
            raise ValueError(f'Missing release file: {name}')
    output.parent.mkdir(parents=True,exist_ok=True)
    with zipfile.ZipFile(output,'w',compression=zipfile.ZIP_DEFLATED) as archive:
        for name in sorted(set(files)):
            archive.write(root/name,'H3-MotionBench/'+name)
    digest=hashlib.sha256(output.read_bytes()).hexdigest()
    output.with_suffix('.zip.sha256').write_text(digest+'  '+output.name+'\n',encoding='ascii')
    print(f'{output}\nSHA256 {digest}\n{len(files)} code/setup files; no local settings, models, runtime or media.')
    return output

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path);args=parser.parse_args()
    root=Path(__file__).resolve().parent
    build(root,args.output or root/'dist/H3-MotionBench.zip')
