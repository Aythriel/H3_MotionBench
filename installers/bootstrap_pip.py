import runpy
import sys

wheel, requirements = sys.argv[1:]
sys.path.insert(0, wheel)
sys.argv = ['pip', 'install', '--disable-pip-version-check', '--require-hashes', '--no-deps', '--no-warn-script-location', '-r', requirements]
runpy.run_module('pip', run_name='__main__')
