# 用户管理系统设计

UserService 负责用户的注册与查询，需求如下：

- register(name, email)：注册用户，返回 user 字典（含 id/name/email）。
  - name 去除首尾空白后非空，否则抛 ValueError。
  - email 必须包含 @，否则抛 ValueError。
  - email 已存在时抛 ValueError。
  - id 从 1 开始自增。
- find_by_email(email)：返回该 email 的用户，不存在返回 None。
- count()：返回已注册用户总数。
- all_names()：按注册顺序返回所有用户名列表。

已有模块：models.make_user、repo.Repo、validators.is_valid_name / is_valid_email。
