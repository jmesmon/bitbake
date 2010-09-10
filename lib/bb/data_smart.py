# ex:ts=4:sw=4:sts=4:et
# -*- tab-width: 4; c-basic-offset: 4; indent-tabs-mode: nil -*-
"""
BitBake Smart Dictionary Implementation

Functions for interacting with the data structure used by the
BitBake build tools.

"""

# Copyright (C) 2003, 2004  Chris Larson
# Copyright (C) 2004, 2005  Seb Frankengul
# Copyright (C) 2005, 2006  Holger Hans Peter Freyther
# Copyright (C) 2005        Uli Luckas
# Copyright (C) 2005        ROAD GmbH
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License version 2 as
# published by the Free Software Foundation.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License along
# with this program; if not, write to the Free Software Foundation, Inc.,
# 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA.
# Based on functions from the base bb module, Copyright 2003 Holger Schurig

import copy, re, sys
from collections import MutableMapping
import logging
import bb
from bb   import utils
from bb.COW  import COWDictBase

logger = logging.getLogger("BitBake.Data")

__setvar_keyword__ = ["_append", "_prepend"]
__setvar_regexp__ = re.compile('(?P<base>.*?)(?P<keyword>_append|_prepend)(_(?P<add>.*))?')
__expand_var_regexp__ = re.compile(r"\${[^{}]+}")
__expand_python_regexp__ = re.compile(r"\${@.+?}")


class DataSmart(MutableMapping):
    def __init__(self, special = COWDictBase.copy(), seen = COWDictBase.copy() ):
        self.dict = {}

        # cookie monster tribute
        self._special_values = special
        self._seen_overrides = seen

        self.expand_cache = {}

    def expand(self, s, varname):
        def var_sub(match):
            key = match.group()[2:-1]
            if varname and key:
                if varname == key:
                    raise Exception("variable %s references itself!" % varname)
            var = self.getVar(key, 1)
            if var is not None:
                return var
            else:
                return match.group()

        def python_sub(match):
            code = match.group()[3:-1]
            codeobj = compile(code.strip(), varname or "<expansion>", "eval")
            value = utils.better_eval(codeobj, {"d": self})
            return str(value)

        if not isinstance(s, basestring): # sanity check
            return s

        if varname and varname in self.expand_cache:
            return self.expand_cache[varname]

        while s.find('${') != -1:
            olds = s
            try:
                s = __expand_var_regexp__.sub(var_sub, s)
                s = __expand_python_regexp__.sub(python_sub, s)
                if s == olds:
                    break
            except Exception:
                logger.exception("Error evaluating '%s'", s)
                raise

        if varname:
            self.expand_cache[varname] = s

        return s

    def finalize(self):
        """Performs final steps upon the datastore, including application of overrides"""

        overrides = (self.getVar("OVERRIDES", True) or "").split(":") or []

        #
        # Well let us see what breaks here. We used to iterate
        # over each variable and apply the override and then
        # do the line expanding.
        # If we have bad luck - which we will have - the keys
        # where in some order that is so important for this
        # method which we don't have anymore.
        # Anyway we will fix that and write test cases this
        # time.

        #
        # First we apply all overrides
        # Then  we will handle _append and _prepend
        #

        for o in overrides:
            # calculate '_'+override
            l = len(o) + 1

            # see if one should even try
            if o not in self._seen_overrides:
                continue

            vars = self._seen_overrides[o]
            for var in vars:
                name = var[:-l]
                try:
                    self[name] = self[var]
                except Exception:
                    logger.info("Untracked delVar")

        # now on to the appends and prepends
        if "_append" in self._special_values:
            appends = self._special_values["_append"] or []
            for append in appends:
                for (a, o) in self.getVarFlag(append, "_append") or []:
                    # maybe the OVERRIDE was not yet added so keep the append
                    if (o and o in overrides) or not o:
                        self.delVarFlag(append, "_append")
                    if o and not o in overrides:
                        continue

                    sval = self.getVar(append, False) or ""
                    sval += a
                    self.setVar(append, sval)


        if "_prepend" in self._special_values:
            prepends = self._special_values["_prepend"] or []

            for prepend in prepends:
                for (a, o) in self.getVarFlag(prepend, "_prepend") or []:
                    # maybe the OVERRIDE was not yet added so keep the prepend
                    if (o and o in overrides) or not o:
                        self.delVarFlag(prepend, "_prepend")
                    if o and not o in overrides:
                        continue

                    sval = a + (self.getVar(prepend, False) or "")
                    self.setVar(prepend, sval)

    def initVar(self, var):
        self.expand_cache = {}
        if not var in self.dict:
            self.dict[var] = {}

    def _findVar(self, var):
        dest = self.dict
        while dest:
            if var in dest:
                return dest[var]

            if "_data" not in dest:
                break
            dest = dest["_data"]

    def _makeShadowCopy(self, var):
        if var in self.dict:
            return

        local_var = self._findVar(var)

        if local_var:
            self.dict[var] = copy.copy(local_var)
        else:
            self.initVar(var)

    def setVar(self, var, value):
        self.expand_cache = {}
        match  = __setvar_regexp__.match(var)
        if match and match.group("keyword") in __setvar_keyword__:
            base = match.group('base')
            keyword = match.group("keyword")
            override = match.group('add')
            l = self.getVarFlag(base, keyword) or []
            l.append([value, override])
            self.setVarFlag(base, keyword, l)

            # todo make sure keyword is not __doc__ or __module__
            # pay the cookie monster
            try:
                self._special_values[keyword].add( base )
            except KeyError:
                self._special_values[keyword] = set()
                self._special_values[keyword].add( base )

            return

        if not var in self.dict:
            self._makeShadowCopy(var)

        # more cookies for the cookie monster
        if '_' in var:
            override = var[var.rfind('_')+1:]
            if override not in self._seen_overrides:
                self._seen_overrides[override] = set()
            self._seen_overrides[override].add( var )

        # setting var
        self.dict[var]["content"] = value

    def getVar(self, var, exp):
        value = self.getVarFlag(var, "content")

        if exp and value:
            return self.expand(value, var)
        return value

    def renameVar(self, key, newkey):
        """
        Rename the variable key to newkey
        """
        val = self.getVar(key, 0)
        if val is not None:
            self.setVar(newkey, val)

        for i in ('_append', '_prepend'):
            src = self.getVarFlag(key, i)
            if src is None:
                continue

            dest = self.getVarFlag(newkey, i) or []
            dest.extend(src)
            self.setVarFlag(newkey, i, dest)

            if i in self._special_values and key in self._special_values[i]:
                self._special_values[i].remove(key)
                self._special_values[i].add(newkey)

        self.delVar(key)

    def delVar(self, var):
        self.expand_cache = {}
        self.dict[var] = {}

    def setVarFlag(self, var, flag, flagvalue):
        if not var in self.dict:
            self._makeShadowCopy(var)
        self.dict[var][flag] = flagvalue

    def getVarFlag(self, var, flag):
        local_var = self._findVar(var)
        if local_var:
            if flag in local_var:
                return copy.copy(local_var[flag])
        return None

    def delVarFlag(self, var, flag):
        local_var = self._findVar(var)
        if not local_var:
            return
        if not var in self.dict:
            self._makeShadowCopy(var)

        if var in self.dict and flag in self.dict[var]:
            del self.dict[var][flag]

    def setVarFlags(self, var, flags):
        if not var in self.dict:
            self._makeShadowCopy(var)

        for i in flags:
            if i == "content":
                continue
            self.dict[var][i] = flags[i]

    def getVarFlags(self, var):
        local_var = self._findVar(var)
        flags = {}

        if local_var:
            for i in local_var:
                if i == "content":
                    continue
                flags[i] = local_var[i]

        if len(flags) == 0:
            return None
        return flags


    def delVarFlags(self, var):
        if not var in self.dict:
            self._makeShadowCopy(var)

        if var in self.dict:
            content = None

            # try to save the content
            if "content" in self.dict[var]:
                content  = self.dict[var]["content"]
                self.dict[var]            = {}
                self.dict[var]["content"] = content
            else:
                del self.dict[var]


    def createCopy(self):
        """
        Create a copy of self by setting _data to self
        """
        # we really want this to be a DataSmart...
        data = DataSmart(seen=self._seen_overrides.copy(), special=self._special_values.copy())
        data.dict["_data"] = self.dict

        return data

    def __iter__(self):
        seen = set()
        def _keys(d):
            if "_data" in d:
                for key in _keys(d["_data"]):
                    yield key

            for key in d:
                if key != "_data":
                    if not key in seen:
                        seen.add(key)
                        yield key
        return _keys(self.dict)

    def __len__(self):
        return len(frozenset(self))

    def __getitem__(self, item):
        return self.getVar(item, False)

    def __setitem__(self, var, value):
        self.setVar(var, value)

    def __delitem__(self, var):
        self.delVar(var)