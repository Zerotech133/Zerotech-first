package com.qianjiangsoft.graphcore.constants;

import lombok.AllArgsConstructor;
import lombok.ToString;

/**
 * @author marshallwu
 * @date 2022/2/24 8:52
 */
@ToString
@AllArgsConstructor
public enum ExtendDirection {

    in("流入"), out("流出"), both("流入或流出");

    public final String label;
}

