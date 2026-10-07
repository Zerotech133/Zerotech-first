package com.qianjiangsoft.graphcore.constants;

import lombok.AllArgsConstructor;
import lombok.ToString;

/**
 * @author marshallwu
 * @date 2022/2/24 8:52
 */
@ToString
@AllArgsConstructor
public enum SearchPathDirection {

    forward("正向"), backward("反向"), undirected("无向");

    public final String label;

}
