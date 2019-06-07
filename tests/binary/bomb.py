def start():
	return 0x401062

def end():
	return [0x4010ee]

def avoid():
	return [0x40143a, 0x4010e9]

def do_start(state):

	arg = None
	for k in range(0, 128):
	    o = state.se.BVS("input_string_" + str(k), 8)
	    state.se.add(state.se.Or(state.se.And(o >= 60, o <= 127), o == 0))

	    if arg == None:
	        arg = o
	    else:
	        arg = state.se.Concat(arg, o)

	# an address where to store my arg
	bind_addr = 0x603780

	# bind the symbolic string at this address
	state.memory.store(bind_addr, arg)

	# phase_5 reads the string [rdi]
	state.regs.rdi = bind_addr

	# make rsi concrete to avoid few uninteresting states
	state.regs.rsi = 0x0

	params = {}
	params['arg'] = arg
	return params

def do_end(state, params, pg, verbose=True):
	if verbose:
		print(state.se.eval(params['arg'], cast_to=bytes))
	arg = params['arg'][1023 : 1024 - (8 * 7)]
	#assert len(state.se.any_n_str(arg, 25000)) == 6400

	sol = [
		[121, 105, 89, 73],
		[63, 127, 79, 111, 95],
		[126, 110, 94, 62, 78],
		[85, 117, 69, 101],
		[118, 102, 70, 86],
		[71, 87, 119, 103],
		[0],
	]

	k = 0
	for b in params['arg'].chop(8):
		s = state.se.eval_upto(b, 256)
		assert set(s) == set(sol[k])
		if len(s) == 1 and s[0] == 0:
			break
		k += 1

	#print state.se.constraints
