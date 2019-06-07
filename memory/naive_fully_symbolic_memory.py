import angr 
import logging
import claripy
import pdb
import simuvex
import sys
import os
import pyvex
import traceback
import bisect
import cffi
import utils
import resource
import paged_memory
import range_map

l = logging.getLogger('naiveFullySymbolicMemory')
l.setLevel(logging.DEBUG)

# profiling vars
time_profile = {}
count_ops = 0
n_ite = 0

def update_counter(elapsed, f):
    
    global time_profile
    global count_ops

    if f not in time_profile:
        time_profile[f] = [1, elapsed]
    else:     
        time_profile[f][0] += 1
        time_profile[f][1] += elapsed
    
    count_ops += 1
    if count_ops > 0 and count_ops % 1000 == 0:
        print()
        print("Profiling stats:") # at depth=" + str(depth) + ":"
        for ff in time_profile:
            print("\t" + str(ff) + ": ncall=" + str(time_profile[ff][0]) + " ctime=" + str(time_profile[ff][1]))

        print("\tMemory footprint: \t" + str(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024) + " MB")
        print()

def print_profiling_stats(depth, pg):

    global time_profile
    global n_ite

    print()
    print("Profiling stats at depth=" + str(depth) + ":")
    print()
    for ff in time_profile:
        print("\t" + str(ff) + ": ncall=" + str(time_profile[ff][0]) + " ctime=" + str(time_profile[ff][1]))

    print()
    print()

    count_leaves = 0
    count_bytes = 0
    count_formulas = 0
    for stash in pg.stashes:
        if len(pg.stashes[stash]) <= 0:
            continue
        print("\tStash " + str(stash) + ":")
        for p in pg.stashes[stash]:
            print("\t\t" + str(len(p.state.memory._concrete_memory)) + ' ' + str(len(p.state.memory._symbolic_memory))) 
            count_leaves += 1
            count_bytes += len(p.state.memory._concrete_memory)
            count_formulas += len(p.state.memory._symbolic_memory)

    print()
    print("\tNumber of leaves: \t" + str(count_leaves))
    print("\tLeaves: overall indexed concrete bytes: \t" + str(count_bytes))
    print("\tLeaves: overall indexed formulas: \t" + str(count_formulas))
    print("\tNumber of states explored: \t" + str(time_profile['__init__'][0])) #angr.path.count_paths)
    print("\tNumber of generated ITE: \t" + str(n_ite))
    print("\tMemory footprint: \t" + str(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024) + " MB")
    print()

def profile(func):
    def wrap(*args, **kwargs):
        import time
        started_at = time.time()
        result = func(*args, **kwargs)
        elapsed = time.time() - started_at
        update_counter(elapsed, func.__name__)
        return result
    return wrap

class MemoryObject(object):

    def __init__(self, obj, offset):
        self.obj = obj
        self.offset = offset
        self.raw_byte = None

    def __repr__(self):
        return "(" + str(self.obj) + " @ " + str(self.offset) + ")"

    def get_byte(self):

        if self.raw_byte is None:
            self.raw_byte = utils.get_obj_bytes(self.obj, self.offset, 1)[0]
            self.obj = self.raw_byte
            self.offset = 0

        return self.raw_byte

    def compare(self, other):
        if type(other) not in (MemoryObject,):
            raise TypeError("Comparing " + str(type(self)) + " with " + str(type(other)) + " is not supported.")

        if self.obj is not None and other.obj is not None:
            if id(self.obj) == id(other.obj) and self.offset == other.offset:
                return True
            else:
                return False
        else:
            if id(self.raw_byte) == id(other.raw_byte) or self.get_byte() == other.get_byte():
                return True
            else:
                return False


class MappedRegion(object):

    PROT_READ = 1
    PROT_WRITE = 2
    PROT_EXEC = 4

    def __init__(self, addr, length, permissions):
        self.addr = addr
        self.length = length
        self.permissions = permissions


    def __repr__(self):
        rwx_s  = "r" if self.is_readable() else ''
        rwx_s += "w" if self.is_writable() else ''
        rwx_s += "x" if self.is_executable() else ''
        return "(" + str(hex(self.addr)) + ", " + str(hex(self.addr + self.length)) + ") [" + rwx_s +"]"


    def is_readable(self):
        return self.permissions.args[0] & MappedRegion.PROT_READ


    def is_writable(self):
        return self.permissions.args[0] & MappedRegion.PROT_WRITE    


    def is_executable(self):
        return self.permissions.args[0] & MappedRegion.PROT_EXEC


class SymbolicMemory(simuvex.plugins.plugin.SimStatePlugin):

    @profile
    def __init__(self, memory_backer=None, 
                permissions_backer=None, 
                kind=None, 
                arch=None, 
                endness=None, 
                check_permissions=None, 
                concrete_memory=None,
                symbolic_memory=None,
                initialized=False,
                stack_range=None,
                mapped_regions=[],
                verbose=False):
        simuvex.plugins.plugin.SimStatePlugin.__init__(self)

        self._memory_backer = memory_backer
        self._permissions_backer = permissions_backer
        self._id = kind
        self._arch = arch
        self._endness = "Iend_BE" if endness is None else endness
        
        self._concrete_memory = concrete_memory if concrete_memory is not None else paged_memory.PagedMemory(self)
        self._symbolic_memory = symbolic_memory if symbolic_memory is not None else range_map.RangeMap()

        self._initialized = initialized

        # some threshold
        self._maximum_symbolic_size = 8 * 1024
        self._maximum_concrete_size = 0x1000000

        self._abstract_backer = None

        # stack range
        self._stack_range = stack_range

        # mapped regions
        self._mapped_regions = mapped_regions

        self.verbose = verbose
        if self.verbose: self.log("symbolic memory has been created")

    @profile
    def _init_memory(self):

        if self._initialized:
            return

        # init mapped regions
        for start, end in self._permissions_backer[1]:

            perms = self._permissions_backer[1][(start, end)]
            self.map_region(start, end-start, perms)

        # init memory
        if self._memory_backer is not None:

            _ffi = cffi.FFI()

            for addr, backer in self._memory_backer.cbackers:

                data = _ffi.buffer(backer)[:]
                obj = claripy.BVV(data)

                if self.verbose: self.log("Initialing memory at " + hex(addr) + " with " + str(len(obj) / 8) + " bytes")

                for k in range(len(obj) / 8):
                    self._concrete_memory[k + addr] = MemoryObject(obj, k)

            if self.verbose: self.log(str(len(self._concrete_memory)) + " concrete bytes in the memory")

        self._initialized = True

    @profile
    def set_state(self, state):
        if self.verbose: self.log("setting current state...")
        self.state = state    
        self._init_memory()

    @profile
    def _raw_ast(self, a):
        if type(a) is simuvex.s_action_object.SimActionObject:
            return a.ast
        elif type(a) is dict:
            return { k:self._raw_ast(a[k]) for k in a }
        elif type(a) in (tuple, list, set, frozenset):
            return type(a)((self._raw_ast(b) for b in a))
        else:
            return a

    @profile
    def memory_op(self, addr, size, data=None):

        addr = self._raw_ast(addr)
        size = self._raw_ast(size)
        data = self._raw_ast(data)

        reg_name = None
        if self._id == 'reg': 

            if type(addr) == int:
                reg_name = utils.reverse_addr_reg(self, addr)
                if self.verbose: self.log("\t" + str(addr) + " => " + str(reg_name))

            if isinstance(addr, str):
                reg_name = addr
                addr, size_reg = utils.resolve_location_name(self, addr)
                if self.verbose: self.log("\t" + str(addr) + " => " + str(reg_name))

                # a load from a register, derive size from reg size
                if size is None:
                    size = size_reg
                    if self.verbose: self.log("\tsize => " + str(size))

                assert size_reg == size

            assert reg_name is not None
            
        # if this is a store then size can be derived from data that needs to be stored
        if size is None and type(data) in (claripy.ast.bv.BV,):
            size = len(data) / 8
            assert type(size) == int
            if self.verbose: self.log("\tsize => " + str(size))

        # convert size to BVV if concrete
        if type(size) == int:
            size = self.state.se.BVV(size, self.state.arch.bits)

        # make size concrete
        if size is not None:
            min_size, max_size = self._resolve_size_range(size)
            size = max_size

        # if addr is constant, make it concrete
        if type(addr) in (claripy.ast.bv.BV,) and not addr.symbolic:
            addr = addr.args[0]

        assert size is not None
        if self._id == 'reg':
            assert type(addr) == int

        return addr, size, reg_name

    @profile
    def _find_concrete_memory(self, a, b):

        assert b >= a
        addresses = []

        if b - a > 1024:

            addrs = sorted(self._concrete_memory.keys()) # expensive, we should keep it sorted across ops
            index = bisect.bisect_left(addrs, a)

            while index < len(addrs):

                if addrs[index] > b: 
                    break

                elif addrs[index] >= a:
                    if addrs[index] in self._concrete_memory:
                        addresses.append(addrs[index])
                index += 1

        else:
            for addr in range(a, b + 1):
                if addr in self._concrete_memory:
                    addresses.append(addr)

        return addresses

    @profile
    def build_ite(self, addr, addrs, v, obj):

        if len(addrs) == 1:
            cond = addr == addrs[0] 
        else:
            cond = self.state.se.And(addr >= addrs[0], addr <= addrs[-1])

        global n_ite
        n_ite += 1

        return self.state.se.If(cond, v, obj)

    @profile
    def load(self, addr, size=None, condition=None, fallback=None, add_constraints=None, action=None, endness=None, inspect=True, ignore_endness=False, disable_actions=False):

        # ToDo
        # assert disable_actions

        global n_ite

        try:

            if self.verbose: self.log("Loading at " + str(addr) + " " + str(size) + " bytes.")

            i_addr = addr
            i_size = size

            assert self._id == 'mem' or self._id == 'reg'

            addr, size, reg_name = self.memory_op(addr, size)        

            if type(size) ==int:

                # concrete address
                if type(addr) == int:
                    min_addr = addr
                    max_addr = addr

                # symbolic addr
                else:
                    min_addr = self.state.se.min_int(addr)
                    max_addr = self.state.se.max_int(addr)
                    if min_addr == max_addr:
                        addr = min_addr

                # check permissions
                self.check_sigsegv_and_refine(addr, min_addr, max_addr, False)

                data = None
                for k in range(size):

                    do_implicit_store = True
                    obj = utils.get_unconstrained_bytes(self.state, "bottom", 8, memory=self)
                    bottom = obj

                    if self.verbose: self.log("\tLoading from: " + str(hex(addr + k) if type(addr) == int else (addr + k)))

                    # check versus concrete addresses
                    concrete_objs = self._concrete_memory.find(min_addr + k, max_addr + k) # move this out of the loop and reuse it
                    if len(concrete_objs) == 1:

                        item = concrete_objs.popitem()
                        v = item[1].get_byte()
                        if min_addr == max_addr: # constant addr
                            obj = v
                            do_implicit_store = False
                        else:
                            n_ite += 1
                            obj = self.state.se.If(addr + k == item[0], v, obj)

                    else:

                        addrs = []
                        concrete_addresses = sorted(concrete_objs.keys())
                        for i in range(len(concrete_addresses)):

                            concrete_addr = concrete_addresses[i]
                            addrs.append(concrete_addr)
                            v = concrete_objs[concrete_addr].get_byte()

                            # lookahead for merging
                            merged = False
                            if i + 1 < len(concrete_addresses) and concrete_addr + 1 == concrete_addresses[i + 1]:

                                next_v = concrete_objs[concrete_addr + 1].get_byte()
                                if v.op == 'BVV':

                                    # both constant and equal
                                    if next_v.op == 'BVV' and v.args[0] == next_v.args[0]:
                                        #if self.verbose: self.log("\tmerging ite with same constant and consecutive address")
                                        merged = True

                                # same symbolic object
                                elif v is next_v:
                                    #if self.verbose: self.log("\tmerging ite with same sym and consecutive address")
                                    merged = True

                            if not merged:
                                if self.verbose:
                                    self.log("\tbuilding ite with " + str(len(addrs)) + " addresses")# " + str(addrs))

                                obj = self.build_ite(addr + k, addrs, v, obj)
                                addrs = []

                        if len(addrs) > 0:
                            if self.verbose: self.log("\tbuilding ite with " + str(len(addrs)) + " addresses") #: "+ str(v))
                            obj = self.build_ite(addr + k, addrs, v, obj)
                            addrs = []

                    # check versus any symbolic address
                    formulas = self._symbolic_memory.query(min_addr + k, max_addr + k)
                    for f in formulas:

                        e = f[2][0]
                        v = f[2][1]
                        range_e = [f[0], f[1]]

                        if self.intersect(e, addr + k, range_e, [min_addr + k, max_addr + k]):
                            if self.verbose: self.log("\tadding ite with symbolic address") #: " + str(v))
                            try:

                                n_ite += 1
                                obj = self.state.se.If(e == addr + k, v.get_byte(), obj)

                            except Exception as e:
                                print(str(e))
                                import pdb
                                pdb.set_trace()

                    if do_implicit_store:
                        if self.verbose: self.log("\tDoing an implicit store...")
                        bottom = MemoryObject(bottom, 0)
                        if min_addr == max_addr:
                            self._concrete_memory[min_addr + k] = bottom
                        else:
                            self._symbolic_memory.add(min_addr + k, max_addr + k, (addr + k, bottom)) # this is wrong, should be an ITE with all negated cases

                    # concat single-byte objs
                    if self.verbose: self.log("\tappending data ")# + str(obj))
                    data = self.state.se.Concat(data, obj) if data is not None else obj

                # fix endness
                endness = self._endness if endness is None else endness
                if not ignore_endness and endness == "Iend_LE":
                    #if self.verbose: self.log("\treversing data: " + str(data))
                    data = data.reversed

                if not disable_actions:
                    if simuvex.o.AST_DEPS in self.state.options and self.category == 'reg':
                        r = simuvex.SimActionObject(data, reg_deps=frozenset((addr,)))

                    if simuvex.o.AUTO_REFS in self.state.options and action is None:
                        ref_size = size if size is not None else (data.size() / 8)
                        region_type = self.category
                        if region_type == 'file':
                            # Special handling for files to keep compatibility
                            # We may use some refactoring later
                            region_type = self.id
                        action = simuvex.SimActionData(self.state, region_type, 'read', addr=addr, data=data, size=ref_size,
                                               condition=condition, fallback=fallback)
                        self.state.log.add_action(action)

                if self.verbose: self.log("\treturning data ")# + str(data))
                return data

            assert False

        except Exception as e:

            if type(e) in (simuvex.s_errors.SimSegfaultError,):
                raise e

            print(str(e))
            import traceback
            traceback.print_exc()
            sys.exit(1)

    @profile
    def store(self, addr, data, size=None, condition=None, add_constraints=None, endness=None, action=None, inspect=True, priv=None, ignore_endness=False, internal=False, disable_actions=False):

        # ToDO
        # assert disable_actions
        global n_ite

        try:

            if not internal:
                if self.verbose: self.log("Storing at " + str(addr) + " " + str(size) + " bytes.") # Content: " + str(data))
                pass

            i_addr = addr
            i_size = size
            i_data = data

            assert self._id == 'mem' or self._id == 'reg'

            addr, size, reg_name = self.memory_op(addr, size, data)

            # convert data to BVV if concrete
            data = utils.convert_to_ast(self.state, data, size if isinstance(size, int) else None)

            if type(size) == int:

                assert len(data) / 8 == size

                # simplify
                data = self.state.se.simplify(data)

                # fix endness
                endness = self._endness if endness is None else endness
                if not ignore_endness and endness == "Iend_LE":
                    if not internal:
                        #if self.verbose: self.log("\treversing data: " + str(data))
                        pass
                    data = data.reversed
                    #if self.verbose: self.log("\treversed data: " + str(data))


                min_addr = None
                max_addr = None

                # concrete address
                if type(addr) == int:
                    min_addr = addr
                    max_addr = addr

                # symbolic addr
                else:
                    min_addr = self.state.se.min_int(addr)
                    max_addr = self.state.se.max_int(addr)
                    if min_addr == max_addr:
                        addr = min_addr

                # check permissions
                self.check_sigsegv_and_refine(addr, min_addr, max_addr, True)

                # conflicting symbolic formulas
                formulas = self._symbolic_memory.query(min_addr, max_addr + size)

                if self.verbose: self.log("\tConflicting formulas: " + str(formulas))

                for k in range(size):

                    obj = MemoryObject(data, k)

                    if not internal:
                        #if self.verbose: self.log("\tSlicing data with offset " + str(k) + " => " + str(obj))
                        pass

                    # concrete addr
                    if type(addr) == int:
                        if not internal:
                            if self.verbose: self.log("\tAdding to concrete memory as: " + str(hex(addr + k)))
                            pass
                        self._concrete_memory[addr + k] = obj

                    flag = False
                    for f in formulas:

                        e = f[2][0]
                        v = f[2][1]
                        range_e = [f[0], f[1]]

                        if not self._symbolic_memory._intersect(range_e[0], range_e[1], min_addr + k, max_addr + k):
                            continue

                        #if self.verbose: self.log("\tEval: " + str(e) + " with " + str(addr + k))

                        # RangeMap may not be accurate
                        if self.disjoint(e, addr + k, range_e, [min_addr + k, max_addr + k]):
                            if self.verbose: self.log("\tDisjoint... skipping")
                            continue

                        elif self.same(e, addr + k, range_e, [min_addr + k, max_addr + k]):
                            if self.verbose: self.log("\tSame... replacing")

                            # if our addr is concrete then we have already a related entry inside the concrete memory
                            # we can safely (?) remove it from the symbolic memory
                            if type(addr + k) == int:
                                self._symbolic_memory.remove(f)
                            else:

                                new_f = (range_e[0], range_e[1], [addr + k, obj])
                                self._symbolic_memory.replace(f, new_f)

                            flag = True

                        else:

                            # the two formulas are not disjoint and are not the same
                            # thus they intesect

                            if self.verbose: self.log("\tIntersect... expanding")
                            try:

                                n_ite += 1
                                new_f = (f[0], f[1], (addr + k, MemoryObject(self.state.se.If(e == addr + k, obj.get_byte(), v.get_byte()), 0)))
                                self._symbolic_memory.replace(f, new_f)

                            except Exception as e:
                                import traceback
                                traceback.print_exc()
                                import pdb
                                print(str(e))
                                pdb.set_trace()

                    if not flag and type(addr) != int:
                        if self.verbose: self.log("\tAdding...")
                        self._symbolic_memory.add(min_addr + k , max_addr + k, (addr + k, obj))

                if not disable_actions:
                    if simuvex.o.AUTO_REFS in self.state.options and action is None and not self._abstract_backer:

                        ref_size = size if size is not None else (data.size() / 8)
                        region_type = self.category
                        if region_type == 'file':
                            # Special handling for files to keep compatibility
                            # We may use some refactoring later
                            region_type = self.id
                        action = simuvex.SimActionData(self.state, region_type, 'write', addr=addr, data=data,
                                               size=ref_size,
                                               condition=condition
                                               )
                        self.state.log.add_action(action)

                        if action is not None:
                            action.actual_value = action._make_object(data)  # TODO

                return

            assert False   

        except Exception as e:

            if type(e) in (simuvex.s_errors.SimSegfaultError,):
                raise e

            import traceback
            print(str(e))
            traceback.print_exc()
            sys.exit(1)

    @profile
    def same(self, a, b, range_a=None, range_b=None):

        # true if the two formulas can cover exactly one address
        # I don't know if there could be other scenarios where this
        # can be true...

        if id(a) == id(b):
            return True
        assert range_a is not None and range_b is not None
        if range_a is not None and range_b is not None and range_a[0] == range_b[0] and range_a[1] == range_b[1] and range_a[1] - range_b[0] == 1:
            return True

        try:
            cond = a != b
            return not self.state.se.satisfiable(extra_constraints=(cond,))
        except Exception as e:
            import traceback
            traceback.print_exc()
            sys.exit(1)

    @profile
    def intersect(self, a, b, range_a=None, range_b=None):
        if id(a) == id(b):
            return True
        assert range_a is not None and range_b is not None
        if range_a is not None and range_b is not None and (range_a[1] < range_b[0] or range_b[1] < range_a[0]):
            return False

        try:
            cond = a == b
            return self.state.se.satisfiable(extra_constraints=(cond,))
        except Exception as e:
            import traceback
            traceback.print_exc()
            sys.exit(1)

    @profile
    def disjoint(self, a, b, range_a=None, range_b=None):
        if id(a) == id(b):
            return False
        assert range_a is not None and range_b is not None 
        if range_a is not None and range_b is not None and (range_a[1] < range_b[0] or range_b[1] < range_a[0]):
            return True
        
        try:
            cond = a == b
            return not self.state.se.satisfiable(extra_constraints=(cond,))
        except Exception as e:
            import traceback
            traceback.print_exc()
            sys.exit(1)

    @profile
    def dump_memory(self):
        pass

    @profile
    def _resolve_size_range(self, size):

        if not self.state.se.symbolic(size):
            i = self.state.se.any_int(size)
            if i > self._maximum_concrete_size:
                raise simuvex.SimMemoryLimitError("Concrete size %d outside of allowable limits" % i)
            return i, i

        max_size = self.state.se.max_int(size)
        min_size = self.state.se.min_int(size)

        # we do not support symbolic size yet...
        if min_size != max_size:
            #print "addr " + str(self.state.ip)
            #print utils.full_stack()
            #assert min_size == max_size
            l.warning("Concretizing symbolic length. Much sad; think about implementing.")
            self.state.add_constraints(size == max_size, action=True)

        if min_size > self._maximum_symbolic_size:
            assert False
            min_size = self._maximum_symbolic_size

        return min_size, min(max_size, self._maximum_symbolic_size)

    @property
    def category(self):
        if self._id in ('reg', 'mem'):
            return self._id

    @profile
    def copy(self):
        if self.verbose: self.log("Copying memory")
        s = SymbolicMemory(memory_backer=self._memory_backer, 
                                permissions_backer=self._permissions_backer, 
                                kind=self._id, 
                                arch=self._arch, 
                                endness=self._endness, 
                                check_permissions=None, 
                                concrete_memory=self._concrete_memory, # we do it properly later since we have to pass to copy() a reference to the new memory
                                symbolic_memory=self._symbolic_memory.copy(),
                                initialized=self._initialized,
                                stack_range=self._stack_range,
                                mapped_regions=self._mapped_regions[:],
                                verbose=self.verbose)

        s._concrete_memory = self._concrete_memory.copy(s)
        return s

    @property
    def id(self):
        return self._id

    @property
    def mem(self):

        # In angr, this returns a reference to the (internal) paged memory
        # We do not have (yet) a paged memory. We instead return self
        # that exposes a _preapproved_stack attribute
        # (similarly as done by a paged memory)

        if self.verbose: self.log("getting reference to paged memory")
        #traceback.print_stack()
        return self

    @property
    def _preapproved_stack(self):
        return self._stack_range

    @_preapproved_stack.setter
    def _preapproved_stack(self, value):
        if self.verbose: self.log("Boundaries on stack have been set by the caller: (" + str(hex(value.start)) + ", " + str(hex(value.end)) + ")")
        
        if self._stack_range is not None:
            if self.verbose: self.log("\tUnnmapping old stack...")
            for k in range(len(self._mapped_regions)):
                region = self._mapped_regions[k]
                if region.addr == self._stack_range.start:
                    del self._mapped_regions[k]
                    if self.verbose: self.log("\tDone.")
                    break

        self._stack_range = value
        self.map_region(value.start, value.end - value.start, MappedRegion.PROT_READ | MappedRegion.PROT_WRITE)

    @profile
    def log(self, msg, verbose=True):
        if verbose:
            l.debug("[" + self._id + "] " + msg)

    @profile
    def error(self, msg):
        l.error("[" + self._id + "] " + msg)

    @profile
    def is_verbose(self, v):
        self.verbose = v
        if not v:
            l.setLevel(logging.INFO)

    @profile
    def map_region(self, addr, length, permissions):

        if self.verbose: self.log("Required mapping of length " + str(length) + " at " + str(hex(addr if type(addr) == int else addr.args[0])) + ".")

        if self.state.se.symbolic(addr) or self.state.se.symbolic(length):
            assert False

        # make if concrete
        if isinstance(addr, claripy.ast.bv.BV):
            addr = self.state.se.max_int(addr)

        # make perms a bitvector to easily check them
        if isinstance(permissions, int):
            permissions = claripy.BVV(permissions, 3)

        # keep track of this region
        self._mapped_regions.append(MappedRegion(addr, length, permissions))

        if self.verbose: self.log("\t" + str(self._mapped_regions[-1]))

        # sort mapped regions 
        self._mapped_regions = sorted(self._mapped_regions, key=lambda x: x.addr)

    @profile
    def unmap_region(self, addr, length):
        assert False

    @profile
    def permissions(self, addr):

        # return permissions of the addr's region

        if self.state.se.symbolic(addr):
            assert False

        if isinstance(addr, claripy.ast.bv.BV):
            addr = self.state.se.any_int(addr)

        for region in self._mapped_regions:
            if addr >= region.addr and addr <= region.addr + region.length:
                return region.permissions

        # Unmapped region?
        assert False

    @profile
    def check_sigsegv_and_refine(self, addr, min_addr, max_addr, write_access):

        if simuvex.o.STRICT_PAGE_ACCESS not in self.state.options:
            return

        # (min_addr, max_addr) is our range addr

        try:

            access_type = "write" if write_access else "read"

            if len(self._mapped_regions) == 0:
                raise simuvex.s_errors.SimSegfaultError(min_addr, "Invalid " + access_type + " access: [" + str(hex(min_addr)) + ", " + str(hex(max_addr)) + "]")

            last_covered_addr = min_addr - 1
            for region in self._mapped_regions:

                # region is after our range addr
                if max_addr < region.addr:
                    break

                # region is before our range addr
                if last_covered_addr + 1 > region.addr + region.length:
                    continue

                # there is one addr in our range that could be not covered by any region
                if last_covered_addr + 1 < region.addr:

                    # check with the solver: is there a solution for addr?
                    if self.state.se.satisfiable(extra_constraints=(addr >= last_covered_addr + 1, addr < region.addr,)):
                        raise simuvex.s_errors.SimSegfaultError(last_covered_addr + 1, "Invalid " + access_type + " access: [" + str(hex(min_addr)) + ", " + str(hex(max_addr)) + "]")

                # last_covered_addr + 1 is inside this region
                # let's check for permissions

                upper_addr = min(region.addr + region.length, max_addr)
                if access_type == 'write':
                    if not region.is_writable() and self.state.se.satisfiable(extra_constraints=(addr >= last_covered_addr + 1, addr <= upper_addr,)):
                        raise simuvex.s_errors.SimSegfaultError(last_covered_addr + 1, "Invalid " + access_type + " access: [" + str(hex(min_addr)) + ", " + str(hex(max_addr)) + "]")

                elif access_type == 'read':
                    if not region.is_readable() and self.state.se.satisfiable(extra_constraints=(addr >= last_covered_addr + 1, addr <= upper_addr,)):
                        raise simuvex.s_errors.SimSegfaultError(last_covered_addr + 1, "Invalid " + access_type + " access: [" + str(hex(min_addr)) + ", " + str(hex(max_addr)) + "]")

                if max_addr > region.addr + region.length:
                    last_covered_addr = region.addr + region.length
                else:
                    last_covered_addr = max_addr

            # last region could not cover up to max_addr
            if last_covered_addr < max_addr:

                # we do not need to check with the solver since max_addr is already a valid solution for addr
                raise simuvex.s_errors.SimSegfaultError(last_covered_addr + 1, "Invalid " + access_type + " access: [" + str(hex(min_addr)) + ", " + str(hex(max_addr)) + "]")

        except Exception as e:

            if type(e) in (simuvex.s_errors.SimSegfaultError,):
                raise e

            print(utils.full_stack())

    @profile
    def merge(self, others, merge_conditions, common_ancestor=None):

        if self.verbose: self.log("Merging memories of " + str(len(others) + 1) + " states")
        assert len(merge_conditions) == 1 + len(others)

        #
        count  = self._merge_concrete_addresses(others, merge_conditions)

        #
        count += self._merge_symbolic_addresses(others, merge_conditions)

        return count > 0

    @profile
    def _merge_concrete_addresses(self, others, merge_conditions, verbose=False):

        assert False
        global n_ite

        if self.verbose: self.log("Merging concrete addresses...")

        count = 0
        all = [self] + others

        # get all in-use addresses among all memories
        if self.verbose: self.log("\tUsed addresses in 0: " + str(len(list(self._concrete_memory.keys()))), verbose)
        addresses = set(self._concrete_memory.keys())
        for k, o in enumerate(others):
            addresses |= set(o._concrete_memory.keys())
            if self.verbose: self.log("\tUsed addresses in " + str(k+1) + ": " + str(len(list(o._concrete_memory.keys()))), verbose)

        if self.verbose: self.log("\tUsed addresses over " + str(len(all)) + " memories: " + str(len(addresses)), verbose)

        # for each address:
        #   - if it is in use in all memories and it has the same byte content then do nothing
        #   - otherwise map the address to an ite with all the possible contents + a bottom case
        if self.verbose: self.log("\tChecking addresses and updating them...", verbose)
        count_same_address = 0
        for addr in addresses:

            same_in_all = True
            values = []
            first_valid_value = None

            for k in range(len(all)):
                m = all[k]
                value = None
                if addr in m._concrete_memory:
                    value = m._concrete_memory[addr]
                    if k > 0 and values[0] is not None and not values[0].compare(value): # ToDo: it is correct to make a comparison? Expensive?
                        same_in_all = False        
                else: 
                    same_in_all = False

                values.append(value)

            if not same_in_all:
                obj = utils.get_unconstrained_bytes(self.state, "bottom", 8, memory=self)
                for k in range(len(values)):
                    value = values[k]
                    if value is None:
                        continue
                    value = value.get_byte()
                    obj = self.state.se.If(merge_conditions[k], value, obj)

                    n_ite += 1

                self._concrete_memory[addr] = MemoryObject(obj, 0)
                if self.verbose: self.log("\tAddr " + str(hex(addr)) + " is replaced with: " + str(obj), verbose)

            else:
                count_same_address += 1

        if self.verbose: self.log("\tConcrete addresses that were the same on all memories:     " + str(count_same_address), verbose)
        if self.verbose: self.log("\tConcrete addresses that were not the same on all memories: " + str(len(addresses) - count_same_address), verbose)

        return count

    @profile
    def _merge_symbolic_addresses(self, others, merge_conditions, verbose=False):

        assert False # ToDo
        if self.verbose: self.log("Merging symbolic addresses...", verbose)

        global n_ite

        count = 0
        all = [self] + others

        symbolic_memory = []
        formulas = {}

        # get all in-use symbolic addresses among all memories
        for k in range(len(all)):

            m = all[k]
            if self.verbose: self.log("\tSymbolic formulas in memory " + str(k) + ": " + str(len(m._symbolic_memory)), verbose)
            for f, v, r in m._symbolic_memory:
            
                found = False
                for ff, V in formulas.items():

                    # do we have the same _exact_ formula?
                    if ff is f:

                        for vv, mems in V.items():
                            # same content?
                            if v.compare(vv):
                                mems.append(k)
                                found = True
                                break

                        if not found:
                            V[v] = [k]
                            found = True
                            break

                if not found:
                    formulas[f] = { v : [k]}

        if self.verbose: self.log("\tSymbolic formulas among all memories: " + str(len(formulas)), verbose)

        if self.verbose: self.log("\tMerging symbolic addresses")
        count_same_address = 0
        for f, V in formulas.items():

            if len(V) == 1 and len(V[list(V.keys())[0]]) == len(all):
                # the same formula with the same content in all memories
                symbolic_memory.append([f, list(V.keys())[0], None])
                count_same_address += 1
                if self.verbose: self.log("\tUnchanged: symbolic address " + str(f) + ": " + str(symbolic_memory[-1][1].get_byte()), verbose)
                continue

            obj = utils.get_unconstrained_bytes(self.state, "bottom", 8, memory=self)
            for v, mems in V.items():

                v = v.get_byte()
                cond = None
                for m in mems:
                    if cond is None:
                        cond = merge_conditions[m]
                    else:
                        cond = self.state.se.Or(cond, merge_conditions[m])

                n_ite += 1

                obj = self.state.se.If(cond, v, obj)

            if self.verbose: self.log("\tSymbolic address " + str(f) + " is replaced with: " + str(obj), verbose)
            symbolic_memory.append([f, MemoryObject(obj, 0), None]) 

        if self.verbose: self.log("\tSymbolic addresses that were the same on all memories:     " + str(count_same_address), verbose)
        if self.verbose: self.log("\tSymbolic addresses that were not the same on all memories: " + str(len(symbolic_memory) - count_same_address), verbose)

        self._symbolic_memory = symbolic_memory

        return count

    
