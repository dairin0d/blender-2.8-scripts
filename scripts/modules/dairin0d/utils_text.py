#  ***** BEGIN GPL LICENSE BLOCK *****
#
#  This program is free software: you can redistribute it and/or modify
#  it under the terms of the GNU General Public License as published by
#  the Free Software Foundation, either version 3 of the License, or
#  (at your option) any later version.
#
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#  GNU General Public License for more details.
#
#  You should have received a copy of the GNU General Public License
#  along with this program.  If not, see <http://www.gnu.org/licenses/>.
#
#  ***** END GPL LICENSE BLOCK *****

import re

#============================================================================#

def split_camelcase(s):
    i0 = 0
    was_upper = True
    for i in range(1, len(s)):
        c = s[i]
        if c.islower():
            if was_upper:
                if (i > i0+1): yield s[i0:i-1]
                i0 = i-1
                was_upper = False
        elif c.isupper():
            if not was_upper:
                if (i > i0): yield s[i0:i]
                i0 = i
                was_upper = True
    i = len(s)
    if (i > i0): yield s[i0:i]

def compress_whitespace(s, keep='INDENTATION'):
    if not s: return s
    if keep == 'INDENTATION': return unindent(s).strip("\r\n")
    if keep != 'NEWLINES': return " ".join(s.split())
    return "\n".join(" ".join(l.split()) for l in s.splitlines())

def indent(s, t):
    if not t: return s
    res = []
    for l in s.splitlines():
        res.append(t + l)
    return "\n".join(res)

def unindent(s, t=None):
    if (not s) or (not s[0].isspace()): return s
    
    lines = s.splitlines()
    if t is None:
        nt = len(s)
        for l in lines:
            nd = len(l) - len(l.lstrip())
            if nd == 0: continue # ignore whitespace-only lines
            nt = min(nt, nd)
            if nt == 0: break
    else:
        nt = len(t)
    
    if nt == 0: return s
    
    res = []
    for l in lines:
        nd = len(l) - len(l.lstrip())
        res.append(l[min(nt, nd):])
    return "\n".join(res)

def split_expressions(s, sep="\t", strip=False):
    if sep == "\t":
        text = s
    else:
        sep = sep.strip()
        text = ""
        brackets = 0
        for c in s:
            if c in "[{(":
                brackets += 1
            elif c in "]})":
                brackets -= 1
            if (brackets == 0) and (c == sep):
                c = "\t"
            text += c
    
    res = text.split("\t")
    return ([s.strip() for s in res] if strip else res)

def math_eval(s):
    try:
        return float(eval(s, math.__dict__))
    except Exception:
        # What actual exceptions can be raised by float/math/eval?
        return None

def vector_to_text(v, sep="\t", axes_names="xyzw"):
    sa = []
    for i in range(len(v)):
        s = str(v[i])
        if axes_names:
            s = axes_names[i] + ": " + s
        sa.append(s)
    return sep.join(sa)

def vector_from_text(v, s, sep="\t", axes_names="xyzw"):
    sa = split_expressions(s, sep, True)
    
    if axes_names:
        # first, check if there are keyword arguments
        kw = False
        
        for a in sa:
            if len(a) < 3:
                continue
            
            try:
                # list has no find() method
                i = axes_names.index(a[0].lower())
            except ValueError:
                i = -1
            
            if (i != -1) and (a[1] == ":"):
                v_i = math_eval(a[2:])
                if v_i is not None:
                    v[i] = v_i
                kw = True
        
        if kw:
            return
    
    for i in range(min(len(v), len(sa))):
        v_i = math_eval(sa[i])
        if v_i is not None:
            v[i] = v_i

# From http://www.bogotobogo.com/python/python_longest_common_substring_lcs_algorithm_generalized_suffix_tree.php
# Actually applicable to any sequence with hashable elements
def longest_common_substring(S, T):
    m = len(S)
    n = len(T)
    counter = [[0]*(n+1) for x in range(m+1)]
    longest = 0
    lcs_set = set()
    for i in range(m):
        for j in range(n):
            if S[i] == T[j]:
                c = counter[i][j] + 1
                counter[i+1][j+1] = c
                if c > longest:
                    longest = c
                    lcs_set = {S[i-c+1:i+1]}
                elif c == longest:
                    lcs_set.add(S[i-c+1:i+1])
    return lcs_set

# Adapted from https://gist.github.com/regularcoder/8254723
def fletcher(data, n): # n should be 16, 32 or 64
    nbytes = min(max(n // 16, 1), 4)
    mod = 2 ** (8 * nbytes) - 1
    sum1 = sum2 = 0
    for i in range(0, len(data), nbytes):
        block = int.from_bytes(data[i:i + nbytes], 'little')
        sum1 = (sum1 + block) % mod
        sum2 = (sum2 + sum1) % mod
    return sum1 + (sum2 * (mod+1))

def hashnames():
    hashnames_codes = [chr(o) for o in range(ord("0"), ord("9")+1)]
    hashnames_codes += [chr(o) for o in range(ord("A"), ord("Z")+1)]
    n = len(hashnames_codes)
    def _hashnames(names):
        binary_data = "\0".join(sorted(names)).encode()
        hash_value = fletcher(binary_data, 32)
        result = []
        while True:
            k = hash_value % n
            result.append(hashnames_codes[k])
            hash_value = (hash_value - k) // n
            if hash_value == 0: break
        return "".join(result)
    return _hashnames
hashnames = hashnames()
