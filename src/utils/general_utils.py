def any_overlap_lowercase_keys(keys: list|set) -> bool:
    return len(set(k.lower() for k in keys)) != len(keys)

def get_grouped_by_lower_keys_dict(data: dict) -> dict:
    lowered = {}
    for k in data:
        if (lower_k := k.lower()) not in lowered:
            lowered[lower_k] = [k]
        else:
            lowered[lower_k].append(k)
    return lowered
    
def map_dict_to_lower_keys(data: dict, force_non_str_keys: bool = False) -> dict:
    if force_non_str_keys:
        str_data = {str(k):v for k,v in data.items()}
        non_str_data = {}
    else:
        str_data, non_str_data = {}, {}
        for k, v in data.items():
            if isinstance(k, str):
                str_data[k] = v
            else:
                non_str_data[k] = v

    if any_overlap_lowercase_keys(str_data):
        grpd_keys = get_grouped_by_lower_keys_dict(str_data)
        overlaps_dict = {k: v for k, v in grpd_keys.items() if len(v) > 1}
        raise ValueError(f'Cannot safely use lower keys: overlaps {overlaps_dict}')
    
    lowered_data = {k.lower(): v for k, v in str_data.items()}
    return lowered_data | non_str_data
    
    
def add_default_key_values(default_values_dict: dict, response_dict: dict):
    return default_values_dict | response_dict
    
    

                

def flatten(L: "Sequence") -> list:
    
    def _flatten_list_gen(L: "Sequence") -> None:
        for item in L:
            if isinstance(item, str):
                yield item
            else:
                try:
                    yield from flatten(item)
                except TypeError:
                    yield item
    
    return list(_flatten_list_gen(L))
                
                



def flatten_dict(d: "MutableMapping", parent_key: str = '', sep: str = '.') -> dict:
    from collections.abc import MutableMapping
    
    def _flatten_dict_gen(d: dict, parent_key, sep=False) -> None:
        for k, v in d.items():
            new_key = parent_key + sep + k if parent_key and sep else k
            if isinstance(v, MutableMapping):
                yield from flatten_dict(v, new_key, sep=sep).items()
            else:
                yield new_key, v
    
    
    return dict(_flatten_dict_gen(d, parent_key, sep))
    
    
def is_collection(o: object):
    return hasattr(o,"__iter__") and not isinstance(o,str)