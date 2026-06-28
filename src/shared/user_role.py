from enum import Enum    

# =========================
# USER ROLES
# =========================

class UserRole(Enum):
    USER = 'user'
    ADMIN = 'admin'
    SYSTEM = 'system'
    
    
def validate_role(role: str|UserRole) -> UserRole|ValueError:
    if isinstance(role, UserRole):
        return role
    return UserRole(role)