export enum EGraphLayoutType {
    'graphin-force' = 'graphin-force',
    'force' = 'force',
    'gForce' = 'gForce',
    'concentric' = 'concentric',
    'radial' = 'radial',
    'circular' = 'circular',
    'dagre' = 'dagre',
    'grid' = 'grid',
    'mds' = 'mds',
    'random' = 'random',
    'comboCombined' = 'comboCombined'
}

export function getPreset(type: string) {
    if (['graphin-force', 'force', 'gForce'].includes(type)) {
        return {
            type: 'concentric'
        }
    }
    return undefined
}

export const pathEdgeTypes = [{
    label: '全部',
    value: '*'
}, {
    label: '通话',
    value: 'account_call'
}, {
    label: '聊天',
    value: 'account_chat'
}, {
    label: '交易',
    value: 'account_trade'
}, {
    label: '账号',
    value: 'suspicion_account'
}]
// 拓展面板-方向下拉
export const directions = [{
    label: '流入',
    value: 'in'
}, {
    label: '流出',
    value: 'out'
}, {
    label: '流入或流出',
    value: 'both'
}]

// 拓展面板-强度下拉
export const strengths = [{
  label: '大于',
  value: '大于'
}, {
  label: '小于',
  value: '小于'
}, {
  label: '等于',
  value: '等于'
},
{
  label: '最大',
  value: '最大'
},
{
  label: '最小',
  value: '最小'
}]
// 连通性分析面板-强度次数下拉
export const graphStrengths = [{
  label: '大于',
  value: '大于'
}, {
  label: '小于',
  value: '小于'
}, {
  label: '等于',
  value: '等于'
}]

export const pathDirections = [{
    label: '有向',
    value: 'REVERSELY'
}, {
    label: '无向',
    value: 'BIDIRECT'
}]


export const dataMap: { [propName: string]: { [propName: string]: string } } = {
    'alipay_account': {
        corporateName: '企业名称',
        fixedPhone: '固定电话',
        country: '国家',
        bindPhone: '绑定手机',
        phone: '手机',
        registerId: '注册使用ID',
        registerDate: '注册时间',
        status: '状态',
        accountId: '用户ID',
        name: '用户名称',
        loginPhone: '登陆手机',
        loginEmail: '登陆邮箱',
        bindBankCard: '绑定银行卡',
        authenticationUseId: '认证使用ID',
        authenticationDate: '认证时间',
        authenticationType: '认证类型',
        accountBalances: '账户余额',
        accountType: '账户类型',
        email: '邮箱',
        isMain: '是否是主端',
        type: '类型'
    },
    'bank_account': {
        tradeCardNum: '交易卡号',
        tradeAccount: '交易账号',
        agent: '代理人',
        agentPhone: '代理人电话',
        useBalance: '可用余额',
        remark: '备注',
        currency: '币种',
        openAccountId: '开户人证件号',
        openAccountPlace: '开户点部',
        openAccountPlaceCode: '开户网点代码',
        openAccountContact: '开户联系方式',
        lastTradeDate: '最后交易时间',
        openAccountDate: '开户时间',
        openAccountBank: '开户银行',
        balance: '余额',
        openAccountName: '开户名称',
        account: '账户余额',
        accountType: '账户类型',
        isMain: '是否是主端',
        type: '类型'
    },
    'chat_user_info': {
        name: '名称',
        account: '账号',
        accountId: '账号ID',
        messageAccount: '消息ID',
        userName: '用户名',
        niceName: '昵称',
        sex: '性别',
        regtime: '注册时间',
        phoneNumber: '电话号码',
        isMain: '是否是主端',
        type: '类型'
    },
    'suspicion_object': {
        identity: '证件号',
        name: '名称',
        type: '类型',
        isMain: '是否主端'
    },
    'account_trade': {
        src: '交易主端账号',
        dst: '交易对端账号',
        in_times: '收入总次数',
        in_money: '收入总金额',
        outTimes: '支出总次数',
        out_money: '支出总金额',
        total_times: '交易总次数',
        total_money: '交易总金额',
        min_date: '最早交易时间',
        max_date: '最近交易时间'
    },
    'account_chat': {
        src: '主端账号',
        dst: '对端账号',
        relation: '账号关系',
        main_remark: '账号对主端备注',
        opposite_remark: '账号对对端备注',
        chat_count: '聊天次数',
        start_date: '开始聊天时间',
        end_date: '最后聊天时间'
    },
    'suspicion_account': {
        src: '主端账号',
        dst: '对端账号',
        type: '类型'
    }
  //   'phone_account': {
  //     account: '账号',
  //     account_id: '账号ID',
  //     // imageType: "phone",
  //     is_main: "main",
  //     // label: "18423579055",
  //     // main: "主端",
  //     // type: "类型"
  // }
}

export const realTypeMap = {
    'suspicion': 'suspicion_object',
    'telegram': 'chat_user_info',
    'email': 'chat_user_info',
    'phone': 'chat_user_info',
    'bank': 'bank_account',
    'alipay': 'alipay_account'
}

// 默认属性
export const initProperty = [
  { label: '强度', value: 'strength', propertyType: 'number' },
  { label: '次数', value: 'count', propertyType: 'number' },
  { label: '出度', value: 'outDegree', propertyType: 'number' },
  { label: '入度', value: 'inDegree', propertyType: 'number' },
  { label: '总度数', value: 'totalDegree', propertyType: 'number' }
]
// 属性为字符串对应下拉框
export const stringProperty = [
  { label: '包含', value: '包含' },
  { label: '不包含', value: '不包含' }
]
// 属性为数字日期对应下拉框
export const numberProperty = [
  { label: '大于', value: '大于' },
  { label: '小于', value: '小于' },
  { label: '等于', value: '等于' },
  { label: '介于', value: '介于' },
  { label: '最大', value: '最大' },
  { label: '最小', value: '最小' }
]
