#!/usr/bin/env python3
"""Offline launcher/path check; the QEMU substitute only records argv."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

repo = Path(__file__).resolve().parent.parent
with tempfile.TemporaryDirectory(prefix='morphos-runtime-check-') as work:
    work = Path(work)
    tools = work / 'repo/tools'
    tools.mkdir(parents=True)
    launcher = tools / 'morphos-run.sh'
    shutil.copy2(repo / 'tools/morphos-run.sh', launcher)
    shutil.copyfile(repo.parent / 'openbios-qemu.elf', work / 'openbios-qemu.elf')
    iso = work / 'fixture.iso'
    iso.touch()
    fake = work / 'record-qemu.py'
    fake.write_text('''#!/usr/bin/env python3
import json, os, sys
if sys.argv[1:] == ['-device', 'help']:
    print('ati-radeon-9700')
elif sys.argv[1:] == ['-audiodev', 'help']:
    print('none')
else:
    with open(os.environ['ARGV_OUT'], 'w') as f: json.dump(sys.argv[1:], f)
''')
    fake.chmod(0o755)
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(('MORPHOS_', 'PPCOSX_', 'R300_', 'PPCGPU_'))}
    env.update(PPCOSX_QEMU=str(fake), MORPHOS_ISO=str(iso),
               MORPHOS_CPUS='', MORPHOS_AUDIO='off', MORPHOS_NET='none',
               MORPHOS_DISPLAY='none', MORPHOS_MACHINE='mac99')
    results = []
    for label, override in [('default', None), ('empty', ''),
                            ('lane-a', str(work / 'lane a')),
                            ('lane-b', str(work / 'lane b'))]:
        current = dict(env, ARGV_OUT=str(work / (label + '.json')))
        if override is not None:
            current['MORPHOS_RUNTIME_DIR'] = override
        subprocess.run(['bash', str(launcher)], env=current, check=True,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        args = json.loads(Path(current['ARGV_OUT']).read_text())
        runtime = Path(override) if override else work / 'repo/vm/morphos'
        assert args[args.index('-bios') + 1] == str(runtime / 'openbios-ppc')
        assert args[args.index('-D') + 1] == str(runtime / 'gpu.log')
        serial = args[args.index('-chardev') + 1].split('logfile=', 1)[1]
        assert Path(serial).parent == runtime
        assert Path(serial).name.startswith('boot-serial-')
        assert (runtime / 'openbios-ppc').is_file()
        results.append({'case': label, 'firmwareSHA256': hashlib.sha256(
            (runtime / 'openbios-ppc').read_bytes()).hexdigest()})
    assert len({r['firmwareSHA256'] for r in results}) == 1
    assert (work / 'lane a/openbios-ppc').stat().st_ino != (
        work / 'lane b/openbios-ppc').stat().st_ino
    print(json.dumps({'status': 'PASS', 'realQEMULaunched': False,
                      'cases': results}, indent=2))
