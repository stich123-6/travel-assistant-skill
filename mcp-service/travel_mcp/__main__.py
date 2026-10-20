"""Operator commands; passwords are entered privately, never as command arguments."""

import argparse
import getpass
import os
from cryptography.fernet import Fernet
from .config import Settings
from .store import Store, ServiceError


def main():
    parser = argparse.ArgumentParser(description='首科行程助手 MCP 服务')
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('generate-key', help='生成服务端加密主密钥，只保存在服务器.env和管理员私密备份中')
    create = commands.add_parser('create-user', help='创建团队成员账号，交互式输入密码')
    create.add_argument('username')
    commands.add_parser('serve', help='运行独立MCP HTTP服务')
    args = parser.parse_args()
    if args.command == 'generate-key':
        print(Fernet.generate_key().decode())
        return
    settings = Settings.from_env()
    if args.command == 'create-user':
        first = getpass.getpass('设置密码（至少12个字符）: ')
        second = getpass.getpass('再次输入密码: ')
        if first != second or len(first) < 12 or len(first) > 1024:
            parser.error('密码不一致，或长度不在12至1024之间')
        try:
            user = Store(settings.data_dir, settings.encryption_key).create_user(args.username, first)
        except ServiceError as error:
            parser.error(error.message)
        print('已创建账号：' + user['username'])
        return
    import uvicorn
    uvicorn.run('travel_mcp.server:create_app', factory=True, host='0.0.0.0', port=8030,
        proxy_headers=True, forwarded_allow_ips=os.environ.get('FORWARDED_ALLOW_IPS', '127.0.0.1'),
        access_log=False, log_level='warning')


if __name__ == '__main__':
    main()
