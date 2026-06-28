from __future__ import annotations
from typing import Any
import datetime as dt
from dataclasses import dataclass
from application.business_validator import Param

# =========================================================
# EXPOSURES
# =========================================================

class ParamExposure:

    def expose(self, param) -> list["Param"]:
        return [param]

    def reconstruct(self, exposed_param: "ExposedParam", args_dict: dict[str, Any]) -> Any:
        param = exposed_param.param
        try:
            return args_dict[param.name]
        except KeyError:
            if not param.required:
                return param.default_value
            raise
        except:
            raise


class DateTimeExposure(ParamExposure):

    def expose(self, param) -> list["Param"]:
        if isinstance(param.default_value, dt.datetime):
            def_date, def_time = param.default_value.date(), param.default_value.time()
        else:
            def_date, def_time = param.default_value, param.default_value
        
        date_param = Param(name=param.name+'_date', param_type=dt.date, default_value=def_date, visible_to=param.visible_to, required=param.required)
        time_param = Param(name=param.name+'_time', param_type=dt.time, default_value=def_time, visible_to=param.visible_to, required=param.required)

        return [date_param, time_param]

    def reconstruct(self, exposed_param: "ExposedParam", args_dict: dict[str, Any]) -> dt.datetime:
        param = exposed_param.param
        date_param, time_param = exposed_param.exposed_params
        
        date_value = super().reconstruct(ExposedParam(date_param, get_param_exposure(date_param)), args_dict)
        time_value = super().reconstruct(ExposedParam(time_param, get_param_exposure(time_param)), args_dict)
        if not param.required and date_value==param.default_value and time_value==param.default_value:
            return param.default_value
        
        return dt.datetime.combine(date_value, time_value)
        
        
# =========================================================
# EXPOSED_PARAM -> (Param, ParamExposure)
# =========================================================

@dataclass
class ExposedParam:
    param: Param
    exposure: ParamExposure

    @property
    def exposed_params(self) -> list[Param]:
        if not hasattr(self, '_exposed_params'):
            self._exposed_params = self.exposure.expose(self.param)
        return self._exposed_params

    def reconstruct(self, args_dict: dict[str, Any]):
        return self.exposure.reconstruct(self, args_dict)        

# =========================================================
# ParamExposure rules
# =========================================================
_param_types_exposures = {dt.datetime: DateTimeExposure()}

def get_param_exposure(param):
    return _param_types_exposures.get(param.param_type, ParamExposure())


# =========================================================
# HELPERS
# =========================================================



def map_param_to_exposed_param(param: Param) -> ExposedParam:
    return ExposedParam(param=param, exposure=get_param_exposure(param))

# =========================================================
# RECONSTRUCTION
# =========================================================
"""
def reconstruct_runtime_args(
    projection: list[ParamProjection],
    args_dict: dict[str, Any]
) -> dict[str, Any]:

    runtime_args = {}

    for p in projection:

        runtime_args[p.param.name] = (
            p.reconstruct(
                p.param,
                args_dict
            )
        )

    return runtime_args
"""