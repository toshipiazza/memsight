#!/bin/bash -e

VIRTUALENV_NAME="memsight"
DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"

pip install virtualenvwrapper virtualenv

# virtualenv
echo "Creating virtualenv"
export WORKON_HOME=$HOME/.virtualenvs
source /usr/share/virtualenvwrapper/virtualenvwrapper.sh
rmvirtualenv $VIRTUALENV_NAME || true
mkvirtualenv $VIRTUALENV_NAME || true

# angr stuff
echo "Installing angr..."
pip install -r $DIR/../requirements.txt
pip install -I --no-use-wheel capstone==3.0.4 # fix error import

# patches
echo "Applying patches"
cd ~/.virtualenvs/$VIRTUALENV_NAME/lib/python2.7/site-packages/

# track angr changes
cd angr; git init; git add . ; git commit -a -m "initial import"; cd ..

for p in $DIR/*.patch; do
    patch -p1 < $p
done

echo
echo "Created virtualenv $VIRTUALENV_NAME. Work on it using: workon $VIRTUALENV_NAME"
echo

exit 0
