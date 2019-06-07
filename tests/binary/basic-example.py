def start():
	return 0x400576

def end():
	return [0x4005bc]

def avoid():
	return [0x4005d5]

def do_start(state):
	params = {}
	params['edi'] = state.regs.edi
	params['esi'] = state.regs.esi
	return params

def do_end(state, params, pg, verbose=True):
	o = state.se.Concat(params['edi'], params['esi'])
	sol = state.se.eval_upto(o, 5)
	import ctypes
	esi = []
	edi = []
	for k in range(len(sol)):
		edi.append(ctypes.c_int((sol[k] & (0xFFFFFFFF << 32)) >> 32).value)
		esi.append(ctypes.c_int(sol[k] & 0xFFFFFFFF).value)
		assert edi[-1] in (2, -2147483646)
		assert esi[-1] in (0,)

	if 	verbose:
		print("EDI: " + str(edi))
		print("ESI: " + str(esi))
		print("Constraints:")
		print(state.se.constraints)
