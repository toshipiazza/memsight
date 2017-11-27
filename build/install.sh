#!/bin/bash -e

VIRTUALENV_NAME="memsight"

# update apt
echo "Updating apt..."
sudo apt-get update >/dev/null || true

# dependencies
echo "Installing dependencies..."
sudo apt-get install -y sudo nano python-pip time git python-dev build-essential
sudo pip install -U pip

# virtualenv
echo "Creating virtualenv"
sudo pip install virtualenv virtualenvwrapper
export WORKON_HOME=$HOME/.virtualenvs
echo "source /usr/local/bin/virtualenvwrapper.sh" >> ~/.bashrc
source ~/.bashrc
mkvirtualenv $VIRTUALENV_NAME || true
# workon memsight

# clone
echo "Cloning..."
if [ ! -d "memsight" ]; then
    cd ~
    git clone git@github.com:season-lab/fully-symbolic-memory.git memsight
fi

# angr stuff
echo "Installing angr..."
pip install -r memsight/requirements.txt
pip install -I --no-use-wheel capstone==3.0.4 # fix error import

# patches
echo "Applying patches"
cd ~/.virtualenvs/$VIRTUALENV_NAME/lib/python2.7/site-packages/
patch -p1 < ~/memsight/build/0001-Fix-wrong-ancestry-in-path-merging-issue-761-772.patch

exit 0