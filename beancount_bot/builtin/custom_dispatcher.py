import datetime
import itertools
from typing import List, Mapping

import yaml

from beancount_bot.dispatcher import Dispatcher
from beancount_bot.i18n import _
from beancount_bot.transaction import NotMatchException
from beancount_bot.util import logger

from xpinyin import Pinyin
py = Pinyin()

_CH_CLASS = [' ', '\"', '\\', '<']
_STATE_MAT = [
    # 空, ", \, <, 其他字符
    [0, 2, -1, 4, 1],  # 0: 空格
    [0, 2, -1, 4, 1],  # 1: 词
    [2, 0, 3, 2, 2],  # 2: 字符串
    [2, 2, 2, 2, 2],  # 3: 转义
    [0, 2, -1, -1, 1],  # 4: 符号
]


def split_command(cmd):
    """
    切分输入指令。按照空格分割，允许使用双引号字符串、反斜杠转义
    :param cmd:
    :return:
    """
    state = 0
    words: List[str] = []

    for i in range(len(cmd)):
        ch = cmd[i]
        # 字符类
        if ch in _CH_CLASS:
            ch_class = _CH_CLASS.index(ch)
        else:
            ch_class = 4
        # 状态转移
        state, old_state = _STATE_MAT[state][ch_class], state
        if state == -1:
            raise ValueError(_("位置 {pos}：语法错误！不应出现符号 {ch}。").format(pos=i, ch=ch))
        # 进入事件
        if state != old_state and old_state != 3:
            if state in [1, 2, 4]:
                words.append('')
            if state in [2, 3]:
                continue
        # 状态事件
        if state != 0:
            words[-1] += ch
    if state not in [0, 1, 4]:
        raise ValueError(_("位置 {pos}：语法错误！字符串、转义未结束。").format(pos=len(cmd)))
    return words


def _to_list(el):
    if isinstance(el, list):
        return el
    return [el]


Template = Mapping


def print_one_usage(template: Template) -> str:
    """
    打印一个模板的语法提示
    :param template:
    :return:
    """
    usage = ''
    # 指令
    command = template['command']
    if isinstance(command, list):
        usage += '(' + '|'.join(command) + ')'
    else:
        usage += command
    # 参数
    if 'args' in template:
        usage += ' ' + ' '.join(template['args'])
    # 可选参数
    if 'optional_args' in template:
        usage += ' ' + ' '.join(map(lambda s: f'[{s}]', template['optional_args']))
    return usage


class CustomDispatcher(Dispatcher):
    """
    模板处理器。通过 Json 模板生成交易信息。
    """

    def get_name(self) -> str:
        return _("模板")

    def get_usage(self) -> str:
        if len(self.templates) > 0:
            command_usage = '\n'.join([f'  - {print_one_usage(t)}' for t in self.templates])
        else:
            command_usage = _("没有定义任何模板")

        default_account = self.config['default_account']

        if len(self.config['accounts']) > 0:
            account_alias = '\n'.join([f'  {k} - {v}' for k, v in self.config['accounts'].items()])
        else:
            account_alias = _("没有定义账户")

        return _('模板指令格式：指令名 必填参数 [可选参数] < 目标账户\n'
                 '  1. 指令名可以有多个，记为”(指令名1|指令名2|...)“；\n'
                 '  2. 目标账户可以省略。省略将使用默认账户\n\n'
                 '当前定义的模板：\n{command_usage}\n\n'
                 '默认账户：{default_account}\n支持的账户：\n{account_alias}') \
            .format(command_usage=command_usage, default_account=default_account, account_alias=account_alias)

    def __init__(self, template_config: str):
        """
        :param template_config: 模板配置文件路径。具体语法参见 template.example.yml
        """
        super().__init__()
        with open(template_config, 'r', encoding='utf-8') as f:
            data = yaml.full_load(f)
        self.config = data['config']
        self.templates = data['templates']

        with open('./accounts/assets.bean', 'r') as f:
            assests = {}
            for vo in f.readlines():
                if 'Assets:' in vo:
                    alias = vo.split(':')[-1].strip().split(' ')[0]
                    full = vo.split(' ')[2].strip()
                    assests[alias] = full
                    assests[py.get_initials(alias, '').lower()] = full

        with open('./accounts/liabilities.bean', 'r') as f:
            liabilities = {}
            for vo in f.readlines():
                if 'Liabilities:' in vo:
                    alias = vo.split(':')[-1].strip().split(' ')[0]
                    full = vo.split(' ')[2].strip()
                    liabilities[alias] = full
                    liabilities[py.get_initials(alias, '').lower()] = full

        with open('./accounts/expenses.bean', 'r') as f:
            expenses = {}
            for vo in f.readlines():
                if 'Expenses:' in vo:
                    alias = vo.split(':')[-1].strip().split(' ')[0]
                    full = vo.split(' ')[2].strip()
                    expenses[alias] = full
                    expenses[py.get_initials(alias, '').lower()] = full
        with open('./accounts/income.bean', 'r') as f:
            income = {}
            for vo in f.readlines():
                if 'Income:' in vo:
                    alias = vo.split(':')[-1].strip().split(' ')[0]
                    full = vo.split(' ')[2].strip()
                    income[alias] = full
                    income[py.get_initials(alias, '').lower()] = full
        self.assests = assests
        self.liabilities = liabilities
        self.expenses = expenses
        self.income = income
        logger.info('加载%s个资金账户', str(len(self.assests.keys()) // 2))
        logger.info('加载%s个信用账户', str(len(self.liabilities.keys()) // 2))
        logger.info('加载%s条支出规则', str(len(self.expenses.keys()) // 2))
        logger.info('加载%s条收入规则', str(len(self.income.keys()) // 2))

        
    def quick_check(self, input_str: str) -> bool:
        words = split_command(input_str)
        prefixes = map(lambda t: _to_list(t['command']), self.templates)
        prefixes = itertools.chain(*prefixes)
        # 开头相同且有空格隔开
        return any(map(lambda prefix: words[0] == prefix, prefixes))

    def _process_raw(self, input_str: str) -> str:
        words = split_command(input_str)
        cmd, args = words[0], words[1:]
        # 选择模板
        template = next(
            filter(lambda t: cmd in _to_list(t['command']), self.templates),
            None
        )
        # 默认参数
        arg_map = {
            'account': self.config['default_account'],
            'date': datetime.date.today().isoformat(),
            'command': cmd,
            'payee': ''
        }
        if template is None:
            if len(args) < 2:
                raise NotMatchException()
            else:
                template = {}
                cmd = cmd.lower()
                price = args[0]
                arg_map['price'] = price
                entity = args[1].lower()
                if not (entity in self.assests or entity in self.liabilities):
                    raise NotMatchException()
                    
                assest = True
                if entity in self.assests:
                    arg_map['account'] = self.assests[entity]
                else:
                    arg_map['account'] = self.liabilities[entity]
                    assest = False
                    
                if not (cmd in self.expenses or cmd in self.income):
                    raise NotMatchException()
                    
                if cmd in self.expenses:
                    # 支出
                    arg_map['action'] = self.expenses[cmd]
                    if assest == True:
                        if cmd == '还款' or cmd == 'hk':
                            entity2 = args[2].lower()
                            arg_map['account2'] = self.liabilities[entity2]
                            template['template'] = '{date} * "{account2}" "{command}"\n  {account2}    +{price} CNY\n  {account}    -{price} CNY\n  {action}'
                        else:
                            template['template'] = '{date} * "{payee}" "{command}"\n  {account}\n  {action}    {price} CNY'
                    else:
                        template['template'] = '{date} * "{payee}" "{command}"\n  {account}    -{price} CNY\n  {action}'
                else:
                    arg_map['action'] = self.income[cmd]
                    if assest == True:
                        template['template'] = '{date} * "{payee}" "{command}"\n  {account}    {price} CNY\n  {action}'
                    else:
                        raise NotMatchException()
                if len(args) >= 3:
                    # 收款人
                    arg_map['payee'] = args[2]
                    
                if len(args) >= 4:
                    # 备注
                    arg_map['command'] = args[3]
                
                
        else:
            # 解析目标账户（<语法）
            if '<' in args:
                split_at = args.index('<')
                args, account = args[:split_at], args[split_at + 1:]
                if len(account) != 1:
                    raise ValueError(_("语法错误！不支持多目标账户。"))
                arg_map['account'] = self.config['accounts'][account[0]]
            # 参数获取
            if 'args' in template:
                args_need = template['args']
                if len(args) < len(args_need):
                    raise ValueError(_("参数过少！语法：{syntax}").format(syntax=print_one_usage(template)))
                arg_map.update({k: v for k, v in zip(args_need, args)})
                args = args[len(args_need):]
            if 'optional_args' in template:
                optional_args = template['optional_args']
                if len(args) > len(optional_args):
                    raise ValueError(_("参数过多！语法：{syntax}").format(syntax=print_one_usage(template)))
                arg_map.update({k: v for k, v in zip(optional_args, args)})
                for empty_k in optional_args[len(args):]:
                    arg_map[empty_k] = ''
                args = args[len(optional_args):]
            if len(args) != 0:
                raise ValueError(_("参数过多！语法：{syntax}").format(syntax=print_one_usage(template)))
            # 计算待计算参数
            if 'computed' in template:
                for k, expr in template['computed'].items():
                    arg_map[k] = eval(expr, None, arg_map)
        # 进行模板替换
        logger.debug('模板参数 %s', arg_map)
        ret = template['template']
        for k, v in arg_map.items():
            ret = ret.replace(f'{{{k}}}', str(v))
        print(ret)
        return ret

