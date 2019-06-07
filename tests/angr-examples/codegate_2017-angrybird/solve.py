#!/usr/bin/env python2
# -*- coding: utf-8 -*-
# Author: David Manouchehri
# Runtime: ~3 minutes

import angr

import sys
import os

sys.path.append(os.path.join(os.path.dirname(__file__), '../../../'))

from memory import factory

START_ADDR = 0x4007c2
FIND_ADDR = 0x404fab  # This is right before the printf

def main(mem_type = 1):
	proj = angr.Project(os.path.dirname(os.path.realpath(__file__)) + '/angrybird')
	# There's a couple anti-run instructions in this binary.
	# Yes, anti-run. That's not a typo.

	plugins = {}
	if mem_type == 1:
		mem_memory, reg_memory = factory.get_range_fully_symbolic_memory(proj)
		plugins['memory'] = mem_memory

	# Because I'm not interested in fixing a weird binary, I'm going to skip all the beginning of the program.
	# this also skips a bunch of initialization, so let's fix that:
	state = proj.factory.entry_state(addr=START_ADDR, plugins=plugins)
	state.regs.rbp = state.regs.rsp
	# using the same values as the binary doesn't work for these variables, I think because they point to the GOT and the binary is using that to try to fingerprint that it's loaded in angr. Setting them to pointers to symbolic memory works fine.
	state.mem[state.regs.rbp - 0x70].long = 0x1000
	state.mem[state.regs.rbp - 0x68].long = 0x1008
	state.mem[state.regs.rbp - 0x60].long = 0x1010
	state.mem[state.regs.rbp - 0x58].long = 0x1018

	sm = proj.factory.simulation_manager(state)  # Create the SimulationManager.
	sm.explore(find=FIND_ADDR)  # This will take a couple minutes. Ignore the warning message(s), it's fine.
	found = sm.found[-1]
	flag = found.posix.dumps(0)

	# This trims off anything that's not printable.
	return flag[:20]

def test():
	assert main() == 'Im_so_cute&pretty_:)'

if __name__ == '__main__':
	import time

	start_time = time.time()
	test()
	print("Elapsed time: " + str(time.time() - start_time))
