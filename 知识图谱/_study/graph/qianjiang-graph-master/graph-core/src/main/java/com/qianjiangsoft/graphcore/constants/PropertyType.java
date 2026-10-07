package com.qianjiangsoft.graphcore.constants;

import lombok.ToString;

/**
 * @author marshallwu
 * @date 2022/2/24 8:52
 */
@ToString
public enum PropertyType {

    string("字符串"), number("数字"), date("日期"), integer("整数");

    public final String name;

    PropertyType(String name) {
        this.name = name;
    }
}
