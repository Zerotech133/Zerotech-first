package com.qianjiangsoft.graphcore.constants;

import lombok.AllArgsConstructor;
import lombok.ToString;

/**
 * @author marshallwu
 * @date 2022/4/12 10:14
 */
@AllArgsConstructor
@ToString
public enum SearchScope {
    name("标签"), property("属性");

    public final String label;
}
