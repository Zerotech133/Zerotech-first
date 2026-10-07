package com.qianjiangsoft.graphcore.param;

import lombok.Data;

/**
 * @author marshallwu
 * @date 2022/6/16 14:38
 */
@Data
public class FilterEdgeParam {
    Condition strengthCondition;
    Condition countCondition;
    Condition timeCondition;
    Condition propertyCondition;
}
