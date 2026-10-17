# SPDX-License-Identifier: LGPL-3.0-only
"""Odoo 18 Chart-Template & ORM Test Adapter for executing upstream l10n_dz and l10n_dz_account_fix tests."""

import csv
import sys
import types
import unittest
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
ODOO_L10N_DZ_DIR = REPO_ROOT / "vendor" / "odoo_18_l10n_dz"
AZINAK_ROOT = REPO_ROOT / "vendor" / "azinak_l10n_dz_account_fix"

_TEMPLATE_REGISTRY: List[Tuple[str, str, Callable]] = []


def _template_decorator(chart_code: str, model_name: str = "template_data") -> Callable:
    def _wrap(func: Callable) -> Callable:
        func._l10n_chart = chart_code
        func._l10n_model = model_name
        _TEMPLATE_REGISTRY.append((chart_code, model_name, func))
        return func

    return _wrap


class _Record:
    def __init__(self, model_name: str, rec_id: int, values: Dict[str, Any], env: "_Env") -> None:
        object.__setattr__(self, "_model_name", model_name)
        object.__setattr__(self, "id", rec_id)
        object.__setattr__(self, "_values", values)
        object.__setattr__(self, "env", env)

    def __getattr__(self, name: str) -> Any:
        vals = object.__getattribute__(self, "_values")
        if name in vals:
            return vals[name]
        return None

    def __setattr__(self, name: str, value: Any) -> None:
        if name in ("_model_name", "id", "_values", "env"):
            object.__setattr__(self, name, value)
        else:
            self._values[name] = value

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, _Record):
            return False
        return self._model_name == other._model_name and self.id == other.id

    def __hash__(self) -> int:
        return hash((self._model_name, self.id))

    def __bool__(self) -> bool:
        return True


class _RecordSet(list):
    @property
    def ids(self) -> List[int]:
        return [r.id for r in self]

    def mapped(self, field_name: str) -> List[Any]:
        return [getattr(r, field_name) for r in self]


class _PropertyField:
    def __init__(self, env: "_Env", model_name: str, field_name: str) -> None:
        self._env = env
        self._model_name = model_name
        self._field_name = field_name

    def get_company_dependent_fallback(self, model_proxy: "_ModelProxy") -> Optional[_Record]:
        company_id = model_proxy.env.company.id
        rec_id = self._env._defaults.get((self._model_name, self._field_name, company_id))
        if rec_id is None:
            return None
        return self._env._records["account.account"].get(rec_id)


class _ModelProxy:
    def __init__(self, env: "_Env", model_name: str) -> None:
        self.env = env
        self.model_name = model_name
        if model_name == "res.partner":
            self._fields = {
                "property_account_receivable_id": _PropertyField(
                    env, "res.partner", "property_account_receivable_id"
                )
            }
        else:
            self._fields = {}

    def sudo(self) -> "_ModelProxy":
        return self

    def with_company(self, company: _Record) -> "_ModelProxy":
        return _ModelProxy(self.env._with_company(company), self.model_name)

    def create(self, vals: Dict[str, Any]) -> _Record:
        next_id = self.env._next_id(self.model_name)
        stored = dict(vals)
        if self.model_name == "account.move":
            stored.setdefault("state", "draft")
            lines = _RecordSet()
            for cmd in stored.get("line_ids", []):
                if isinstance(cmd, (list, tuple)) and len(cmd) == 3:
                    lvals = dict(cmd[2])
                    debit = lvals.get("debit", 0)
                    credit = lvals.get("credit", 0)
                    lvals["balance"] = debit - credit
                    lid = self.env._next_id("account.move.line")
                    lrec = _Record("account.move.line", lid, lvals, self.env)
                    self.env._records["account.move.line"][lid] = lrec
                    lines.append(lrec)
            stored["line_ids"] = lines
        rec = _Record(self.model_name, next_id, stored, self.env)
        if self.model_name == "account.move":
            rec.action_post = lambda: setattr(rec, "state", "posted")  # type: ignore
        self.env._records[self.model_name][next_id] = rec
        return rec

    def search(self, domain: List[Any], limit: Optional[int] = None) -> Any:
        all_recs = list(self.env._records[self.model_name].values())
        matched = _RecordSet()
        for r in all_recs:
            ok = True
            for f, op, val in domain:
                rval = getattr(r, f)
                if op == "=" and rval != val:
                    ok = False
                    break
                if op == "in":
                    target_set = val if isinstance(val, (list, tuple, set)) else [val]
                    rset = rval if isinstance(rval, (list, tuple, set)) else [rval]
                    if not any(x in target_set for x in rset):
                        ok = False
                        break
            if ok:
                matched.append(r)
                if limit and len(matched) >= limit:
                    break
        if limit == 1:
            return matched[0] if matched else None
        return matched

    def set(self, model_name: str, field_name: str, value: int, company_id: int) -> None:
        self.env._defaults[(model_name, field_name, company_id)] = value

    def _get_chart_template_data(self, chart_code: str) -> Dict[str, Any]:
        data: Dict[str, Any] = {"template_data": {}, "account.account": {}, "res.company": {}}
        # 1. Load official CSV accounts
        with (ODOO_L10N_DZ_DIR / "data" / "template" / "account.account-dz.csv").open(encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                xml_id = row["id"].strip()
                data["account.account"][xml_id] = {
                    "name": row["name"].strip(),
                    "code": row["code"].strip(),
                    "account_type": row["account_type"].strip(),
                    "reconcile": row["reconcile"].strip() == "True",
                    "deprecated": False,
                    "active": True,
                }
        # 2. Apply registered @template methods in order (official first, then azinak fix)
        for c_code, model_key, func in _TEMPLATE_REGISTRY:
            if c_code != chart_code:
                continue
            res = func(self)
            if model_key == "template_data":
                data["template_data"].update(res)
            elif model_key == "account.account":
                for xml_id, overrides in res.items():
                    data["account.account"].setdefault(xml_id, {}).update(overrides)
            elif model_key == "res.company":
                data["res.company"].update(res)
        return data

    def _get_chart_template_model_data(self, chart_code: str, model_name: str) -> Dict[str, Any]:
        return self._get_chart_template_data(chart_code)[model_name]

    def try_loading(self, chart_code: str, company: _Record, install_demo: bool = False, force_create: bool = True) -> None:
        chart_data = self._get_chart_template_data(chart_code)
        company.chart_template = chart_code
        company.account_fiscal_country_id = self.env.ref("base.dz")

        # Create general journal if absent
        existing_j = self.env["account.journal"].search([("company_id", "=", company.id), ("type", "=", "general")], limit=1)
        if not existing_j:
            self.env["account.journal"].create({"name": "Miscellaneous Operations", "type": "general", "company_id": company.id})

        for xml_id, avals in chart_data["account.account"].items():
            ref_key = (company.id, xml_id)
            if ref_key in self.env._company_xmlid_map:
                continue
            rec = self.env["account.account"].create(
                {
                    "name": avals["name"],
                    "code": avals["code"],
                    "account_type": avals["account_type"],
                    "reconcile": avals["reconcile"],
                    "deprecated": avals.get("deprecated", False),
                    "active": avals.get("active", True),
                    "company_ids": [company.id],
                }
            )
            self.env._company_xmlid_map[ref_key] = rec

        rec_xmlid = chart_data["template_data"].get("property_account_receivable_id")
        if rec_xmlid:
            rec_acct = self.env._company_xmlid_map.get((company.id, rec_xmlid))
            if rec_acct:
                self.env["ir.default"].set(
                    "res.partner",
                    "property_account_receivable_id",
                    rec_acct.id,
                    company_id=company.id,
                )

    def ref(self, xml_id: str, raise_if_not_found: bool = True) -> Optional[_Record]:
        rec = self.env._company_xmlid_map.get((self.env.company.id, xml_id))
        if rec is None and raise_if_not_found:
            raise ValueError(f"XML ID {xml_id} not found for company {self.env.company.id}")
        return rec


class _Env:
    def __init__(self) -> None:
        self._id_counters: Dict[str, int] = {}
        self._records: Dict[str, Dict[int, _Record]] = {
            "res.country": {},
            "res.company": {},
            "res.partner": {},
            "account.account": {},
            "account.journal": {},
            "account.move": {},
            "account.move.line": {},
        }
        self._global_refs: Dict[str, _Record] = {}
        self._company_xmlid_map: Dict[Any, _Record] = {}
        self._defaults: Dict[Any, int] = {}
        self._models: Dict[str, Any] = {}

        dz = _Record("res.country", 1, {"code": "DZ", "name": "Algeria"}, self)
        us = _Record("res.country", 2, {"code": "US", "name": "United States"}, self)
        self._records["res.country"][1] = dz
        self._records["res.country"][2] = us
        self._global_refs["base.dz"] = dz
        self._global_refs["base.us"] = us

        default_comp = _Record("res.company", 1, {"name": "Default", "country_id": 1}, self)
        self._records["res.company"][1] = default_comp
        self.company = default_comp

    def _next_id(self, model_name: str) -> int:
        cur = self._id_counters.get(model_name, 10) + 1
        self._id_counters[model_name] = cur
        return cur

    def _with_company(self, company: _Record) -> "_Env":
        clone = object.__new__(_Env)
        clone._id_counters = self._id_counters
        clone._records = self._records
        clone._global_refs = self._global_refs
        clone._company_xmlid_map = self._company_xmlid_map
        clone._defaults = self._defaults
        clone._models = self._models
        clone.company = company
        return clone

    def ref(self, xml_id: str) -> _Record:
        return self._global_refs[xml_id]

    def __getitem__(self, model_name: str) -> Any:
        if model_name == "l10n_dz_account_fix.migration":
            mig_cls = self._models["l10n_dz_account_fix.migration"]
            inst = mig_cls()
            inst.env = self
            return inst
        return _ModelProxy(self, model_name)


def install_odoo_shim_and_load_modules() -> None:
    """Install lightweight `odoo` module shim into sys.modules and load official + azinak modules."""
    if "odoo" in sys.modules and getattr(sys.modules["odoo"], "_is_dz_erp_shim", False):
        return

    _TEMPLATE_REGISTRY.clear()

    odoo_mod = types.ModuleType("odoo")
    odoo_mod._is_dz_erp_shim = True  # type: ignore

    models_mod = types.ModuleType("odoo.models")

    class AbstractModel:
        _name: Optional[str] = None
        _inherit: Optional[str] = None
        _description: Optional[str] = None
        env: _Env

    class Model(AbstractModel):
        pass

    models_mod.AbstractModel = AbstractModel  # type: ignore
    models_mod.Model = Model  # type: ignore
    odoo_mod.models = models_mod  # type: ignore

    addons_mod = types.ModuleType("odoo.addons")
    account_mod = types.ModuleType("odoo.addons.account")
    account_models_mod = types.ModuleType("odoo.addons.account.models")
    chart_template_mod = types.ModuleType("odoo.addons.account.models.chart_template")
    chart_template_mod.template = _template_decorator  # type: ignore

    tests_mod = types.ModuleType("odoo.tests")
    tests_common_mod = types.ModuleType("odoo.tests.common")

    class TransactionCase(unittest.TestCase):
        env: _Env

        @classmethod
        def setUpClass(cls) -> None:
            super().setUpClass()
            cls.env = _Env()
            cls.env._models["l10n_dz_account_fix.migration"] = mig_mod.AlgerianAccountingFixMigration

    def tagged(*_args: Any) -> Callable:
        def _decorator(cls: Any) -> Any:
            return cls

        return _decorator

    tests_common_mod.TransactionCase = TransactionCase  # type: ignore
    tests_common_mod.tagged = tagged  # type: ignore

    sys.modules["odoo"] = odoo_mod
    sys.modules["odoo.models"] = models_mod
    sys.modules["odoo.addons"] = addons_mod
    sys.modules["odoo.addons.account"] = account_mod
    sys.modules["odoo.addons.account.models"] = account_models_mod
    sys.modules["odoo.addons.account.models.chart_template"] = chart_template_mod
    sys.modules["odoo.tests"] = tests_mod
    sys.modules["odoo.tests.common"] = tests_common_mod

    # Load official Odoo 18.0 template_dz.py first
    official_code = (ODOO_L10N_DZ_DIR / "models" / "template_dz.py").read_text(encoding="utf-8")
    official_mod = types.ModuleType("odoo.addons.l10n_dz.models.template_dz")
    exec(compile(official_code, str(ODOO_L10N_DZ_DIR / "models" / "template_dz.py"), "exec"), official_mod.__dict__)

    # Load azinak/l10n-dz-account-fix template_dz.py and migration.py
    if str(AZINAK_ROOT) not in sys.path:
        sys.path.insert(0, str(AZINAK_ROOT))
    import l10n_dz_account_fix.models.template_dz  # noqa: F401
    import l10n_dz_account_fix.models.migration as mig_mod
