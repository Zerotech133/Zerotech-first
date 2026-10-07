package com.qianjiangsoft.graphcore.constants;

import lombok.AllArgsConstructor;
import lombok.ToString;

/**
 * @author marshallwu
 * @date 2022/2/24 8:52
 */
@ToString
@AllArgsConstructor
public enum SearchMode {

    vertex("实体"), edge("连接"), all("实体和连接");

    public final String label;

}
