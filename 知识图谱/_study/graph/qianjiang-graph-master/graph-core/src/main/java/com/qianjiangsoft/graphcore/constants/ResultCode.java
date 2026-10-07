package com.qianjiangsoft.graphcore.constants;


import lombok.Getter;

public enum ResultCode {

    SUCCESS("200","ok"),

    PARAM_ERROR("A0001","参数错误"),

    BUSINESS_ERROR("B0001","业务错误"),
    BUSINESS_DUPLICATE_RECORD_ERROR("B0002","重复数据"),
    BUSINESS_REPEAT_SUBMIT("B0003","重复提交请求"),

    DATA_NOT_EXISTS("B0004","数据不存在"),

    TASK_UNKNOWN("C0001", "任务调度错误"),
    TASK_EXISTS("C0002", "任务已经存在"),
    NO_TASK_EXISTS("c0003", "不存在任务"),
    TASK_ALREADY_STARTED("C0004", "任务已经启动"),
    TASK_CONFIG_ERROR("C0005","任务配置错误"),
    TASK_NODE_NOT_AVAILABLE("C0006","任务节点不可用"),


    UTIL_ERROR("D0001", "工具类错误"),

    SQL_ERROR("E0001", "SQL错误"),

    ACCOUNT_ERROR("F0001", "账户类错误"),

    FILE_ERROR("G0001", "文件类错误"),
    FILE_SIZE_ERROR("G0002", "文件大小超出范围"),
    FILE_TYPE_ERROR("G0003", "文件类型不正确"),

    AUTHENTICATION_ERROR("H0001", "身份认证出错"),

    AUTHORIZATION_ERROR("I0001", "权限不足"),

    HTTP_ERROR("J0001", "http访问出错"),
    HTTP_NOT_FOUND_ERROR("J0002", "访问的资源不存在"),

    TABLE_DATA_IMPORT_ERROR("J0001", "数据导入出错");


    @Getter
    String code;

    @Getter
    String msg;


    ResultCode(String code, String msg) {
        this.code = code;
        this.msg = msg;
    }
}
