from shared.user_role import UserRole, validate_role


class User:
    def __init__(self, user_id: str, user_role: UserRole, nickname: str = None):
        self.user_id = user_id
        self.user_role = user_role
        self.nickname = nickname if nickname is not None else user_id


class UsersToRoleDB:
    """ Keeps track of {user_id: user_role}. Only keeps track of users being either ADMIN or SYSTEM.
    All the other ones are USER by default and wont even be part of the dict.
    """
    def __init__(self, users: list[User] = None):
        self._users = {}
        if users:
            for u in users:
                self.upsert_user(u)
        
    def upsert_user(self, user: User):
        self._users[user.user_id]=user
            
    def get_user(self, user_id: str):
        return self._users.get(user_id)
            
    def remove_user(self, user_id: str):
        self._users.pop(user_id, None)
        
    def get_user_role(self, user_id: str):
        if user_id not in self._users:
            return UserRole.USER
        return self._users[user_id].user_role        
        
    def is_admin(self, user_id: str):
        return self.get_user_role(user_id)==UserRole.ADMIN
        
    def store(self, path):
        import json
        
        with open(path, 'w') as f:
            json.dump({k:v.__dict__ for k,v in self._users.items()}, f, default=str)
        return
       
    @classmethod
    def from_disk(cls, path):
        import json
        from pathlib import Path
        if not Path(path).exists():
            return cls()
        
        with open(path, 'r') as f:
            users_dct = json.load(f)
        users = [User(**user_dct) for user_dct in users_dct.values()]
        return cls(users)
    

        