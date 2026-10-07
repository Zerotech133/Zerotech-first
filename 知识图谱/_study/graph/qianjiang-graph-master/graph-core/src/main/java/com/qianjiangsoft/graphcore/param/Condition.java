package com.qianjiangsoft.graphcore.param;

import lombok.Data;

/**
 * @author marshallwu
 * @date 2022/3/2 17:16
 */
@Data
public class Condition {
    String key;
    String conditionType;
    String value;
    String value2;
}
