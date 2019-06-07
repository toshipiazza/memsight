import angr
import claripy
import os
import sys

sys.path.append(os.path.join(os.path.dirname(__file__), '../..'))

from memory import factory


def check(state, obj, exp_values, conditions=()):
    r = state.se.eval_upto(obj, len(exp_values) + 1, extra_constraints=conditions,
            cast_to=int)
    if len(r) != len(exp_values) or set(r) != set(exp_values):
        print("Mismatch:")
        print("\tobtained: " + str(r))
        print("\texpected: " + str(exp_values))
    assert len(r) == len(exp_values) and set(r) == set(exp_values)

def test_store_with_symbolic_size(state):

    val = 0x01020304
    state.memory.store(0x0, claripy.BVV(val, 32))
    res = state.memory.load(0x0, 4)
    assert not state.se.symbolic(res) and state.se.eval(res, cast_to=int) == val

    val_2 = 0x0506
    s_size = claripy.BVS('size', 32)
    state.se.add(s_size <= 2)
    state.memory.store(0x1, claripy.BVV(val_2, 16), s_size)
    res = state.se.eval_upto(state.memory.load(0x0, 4), 10, cast_to=int)
    # print ' '.join([hex(x) for x in res])
    assert len(state.se.eval_upto(state.memory.load(0x0, 4), 10, cast_to=int)) == 3

    s0 = state.copy()
    assert len(s0.se.eval_upto(s0.memory.load(0x0, 4), 10, cast_to=int)) == 3

    s1 = state.copy()
    s1.se.add(s_size == 0)
    res = s1.se.eval_upto(s1.memory.load(0x0, 4), 2, cast_to=int)
    assert len(res) == 1 and res[0] == val

    s2 = state.copy()
    s2.se.add(s_size == 1)
    res = s2.se.eval_upto(s2.memory.load(0x0, 4), 2, cast_to=int)
    assert len(res) == 1 and res[0] == 0x01050304

    s3 = state.copy()
    s3.se.add(s_size == 2)
    res = s3.se.eval_upto(s3.memory.load(0x0, 4), 2, cast_to=int)
    assert len(res) == 1 and res[0] == 0x01050604


def test_store_with_symbolic_addr_and_symbolic_size(state):

    #state.memory.set_verbose(True)

    val = 0x01020304
    addr = claripy.BVS('addr', 64)
    state.se.add(addr < 8)

    state.memory.store(addr, claripy.BVV(val, 32), 4)

    res = state.memory.load(addr, 4)

    res = state.se.eval_upto(res, 20, cast_to=int)
    assert len(res) == 1 and res[0] == val

    val_2 = 0x0506
    s_size = claripy.BVS('size', 32)
    state.se.add(s_size <= 2)
    state.memory.store(addr + 1, claripy.BVV(val_2, 16), s_size)

    s0 = state.copy()
    assert len(s0.se.eval_upto(s0.memory.load(addr, 4), 10, cast_to=int)) == 3

    s1 = state.copy()
    s1.se.add(s_size == 0)
    res = s1.se.eval_upto(s1.memory.load(addr, 4), 2, cast_to=int)
    assert len(res) == 1 and res[0] == val

    s2 = state.copy()
    s2.se.add(s_size == 1)
    res = s2.se.eval_upto(s2.memory.load(addr, 4), 2, cast_to=int)
    assert len(res) == 1 and res[0] == 0x01050304

    s3 = state.copy()
    s3.se.add(s_size == 2)
    res = s3.se.eval_upto(s3.memory.load(addr, 4), 2, cast_to=int)
    assert len(res) == 1 and res[0] == 0x01050604

def test_concrete_merge(state):
    val = 0x01020304
    state.memory.store(0x0, claripy.BVV(val, 32))

    s1 = state.copy()
    s2 = state.copy()

    s1.memory.store(0x1, claripy.BVV(0x05, 8))
    s2.memory.store(0x1, claripy.BVV(0x06, 8))

    s3 = s1.copy()
    guard = claripy.BVS('branch', 32)
    s3.memory.merge([s2.memory], [guard > 0, guard <= 0], state.memory)

    res = s3.memory.load(0x0, 4)

    r1 = s3.se.eval_upto(res, 2, extra_constraints=(guard > 0,), cast_to=int)
    assert len(r1) == 1 and r1[0] == 0x01050304

    r2 = s3.se.eval_upto(res, 2, extra_constraints=(guard <= 0,), cast_to=int)
    assert len(r2) == 1 and r2[0] == 0x01060304

def test_concrete_merge_with_condition(state):

    val = 0x01020304
    state.memory.store(0x0, claripy.BVV(val, 32))

    s1 = state.copy()
    s2 = state.copy()

    s1.memory.store(0x1, claripy.BVV(0x05, 8))

    cond = claripy.BVS('cond', 32)
    s2.memory.store(0x1, claripy.BVV(0x06, 8), condition=cond != 0)

    s3 = s1.copy()
    guard = claripy.BVS('guard', 32)
    s3.memory.merge([s2.memory], [guard > 1, guard <= 1], s1.memory)

    res = s3.memory.load(0x0, 4)

    r1 = s3.se.eval_upto(res, 2, extra_constraints=(guard > 1,), cast_to=int)
    assert len(r1) == 1 and r1[0] == 0x01050304

    r2 = s3.se.eval_upto(res, 3, extra_constraints=(guard <= 1,), cast_to=int)
    assert len(r2) == 2 and set(r2) == set([0x01020304, 0x01060304])

    s4 = s3.copy()
    s4.se.add(guard == 1)
    s4.se.add(cond != 0)
    res = s4.memory.load(0x0, 4)
    r3 = s4.se.eval_upto(res, 2, cast_to=int)
    assert len(r3) == 1 and r3[0] == 0x01060304

def test_symbolic_merge(state):

    val = 0x01020304
    state.memory.store(0x0, claripy.BVV(val, 32))

    a = claripy.BVS('a0', 64)
    state.se.add(a <= 1)
    state.memory.store(a, claripy.BVV(0x5, 8))

    res = state.memory.load(0x0, 1)
    check(state, res, [5, 1])

    s1 = state.copy()
    s1.memory.store(0x1, claripy.BVV(0x6, 8))
    a1 = claripy.BVS('a1', 64)
    s1.se.add(a1 >= 1)
    s1.se.add(a1 <= 2)
    s1.memory.store(a1, claripy.BVV(0x7, 8))

    s2 = state.copy()
    s2.memory.store(0x1, claripy.BVV(0x8, 8))
    a2 = claripy.BVS('a2', 64)
    s2.se.add(a2 >= 1)
    s2.se.add(a2 <= 2)
    s2.memory.store(a2, claripy.BVV(0x9, 8))

    s3 = s1.copy()
    guard = claripy.BVS('guard', 32)
    s3.memory.merge([s2.memory], [guard > 1, guard <= 1], s1.memory)

    res = s3.memory.load(0x0, 1)
    check(s3, res, [5], (a == 0,))
    check(s3, res, [1], (a == 1,))

    res = s3.memory.load(0x1, 1)
    check(s3, res, [7], (guard > 1, a1 == 1, ))
    check(s3, res, [6], (guard > 1, a1 == 2,))
    check(s3, res, [9], (guard <= 1, a2 == 1,))
    check(s3, res, [8], (guard <= 1, a2 == 2,))

    res = s3.memory.load(0x2, 1)
    check(s3, res, [7], (guard > 1, a1 == 2,))
    check(s3, res, [3], (guard > 1, a1 == 1,))
    check(s3, res, [9], (guard <= 1, a2 == 2,))
    check(s3, res, [3], (guard <= 1, a2 == 1,))

    res = s3.memory.load(0x3, 1)
    check(s3, res, [4], set())

def test_symbolic_access(state):

    # an address which is in a valid region
    start_addr = state.heap.heap_location
    state.heap.heap_location += 32  # mark 32 bytes as used

    #assert state.se.eval(state.memory.permissions(start_addr), cast_to=int) == 0x3
    #assert state.se.eval(state.memory.permissions(start_addr + 1), cast_to=int) == 0x3
    #assert state.se.eval(state.memory.permissions(start_addr + 2), cast_to=int) == 0x3

    # init memory 3 bytes starting at start_addr
    state.memory.store(start_addr, claripy.BVV(0x0, 24), 3)

    # a symbolic pointer that can be equal to [start_addr, start_addr + 1]
    addr = claripy.BVS('addr', 64)
    state.se.add(addr >= start_addr)
    state.se.add(addr <= start_addr + 1)
    addrs = state.se.eval_upto(addr, 10, cast_to=int)
    assert len(addrs) == 2 and set(addrs) == set([start_addr, start_addr + 1])

    val = 0xABCD

    # symbolic store at addr
    state.memory.store(addr, claripy.BVV(val, 16), 2)

    # symbolic load at addr
    res = state.memory.load(addr, 2)
    res = state.se.eval_upto(res, 20, cast_to=int)
    assert len(res) == 1 and res[0] == val


def test_same_operator(state):

    a = claripy.BVS('a', 8)
    b = claripy.BVS('b', 8)

    assert not state.memory.same(a, b)

    state.se.add(a == b)

    assert state.memory.same(a, b)

    state.se.add(a < 5)

    assert state.memory.same(a, b)

    zero = claripy.BVV(0x0, 8)
    assert not state.memory.same(a, zero)

    state.se.add(a < 1)

    assert state.memory.same(a, zero)

if __name__ == '__main__':

    t = 1
    angr_project = angr.Project("/bin/ls", load_options={'auto_load_libs': False})

    if t == 0:
        mem_memory, reg_memory = None, None
    elif t == 1:
        mem_memory, reg_memory = factory.get_range_fully_symbolic_memory(angr_project)
        #mem_memory.set_verbose(True)

    plugins = {}
    if mem_memory is not None:
        plugins['memory'] = mem_memory
    if reg_memory is not None:
        plugins['registers'] = reg_memory

    add_options = set()
    #add_options = {simuvex.o.STRICT_PAGE_ACCESS}
    # add_options = {simuvex.o.CGC_ZERO_FILL_UNCONSTRAINED_MEMORY}

    state = angr_project.factory.entry_state(remove_options={angr.options.LAZY_SOLVES},
                                             add_options=add_options, plugins=plugins)

    if t == 0:
        # store: add new concretization strategy
        state.memory.write_strategies.insert(0, angr.concretization_strategies.SimConcretizationStrategyRange(2048))
        state.memory.read_strategies.insert(0, angr.concretization_strategies.SimConcretizationStrategyRange(2048))
        pass

    test_symbolic_access(state.copy())

    test_store_with_symbolic_size(state.copy())
    test_store_with_symbolic_addr_and_symbolic_size(state.copy())

    test_concrete_merge(state.copy())
    test_concrete_merge_with_condition(state.copy())

    test_symbolic_merge(state.copy())

    if t == 1:
        test_same_operator(state.copy())

