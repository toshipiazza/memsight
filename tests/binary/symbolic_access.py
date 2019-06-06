def start():
	return 0x80483ed

def end():
	return [0x80483ec]

def avoid():
	return [0x8048405]

def do_start(state):
	import angr
	import sys

	if int(sys.argv[1]) == 1:
		state.memory.write_strategies.insert(0, angr.concretization_strategies.SimConcretizationStrategyRange(2048))
		state.memory.read_strategies.insert(0, angr.concretization_strategies.SimConcretizationStrategyRange(2048))

	params = {}
	return params

def do_end(state, params, pg, verbose):

	v = state.regs.eax
	sol = state.se.eval_upto(v, 10)
	print(sol)

