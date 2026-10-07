package com.qianjiangsoft.graphcore.param;

import com.fasterxml.jackson.annotation.JsonValue;
import com.qianjiangsoft.graphcore.constants.SortingDirection;
import com.qianjiangsoft.graphcore.constants.SortingType;
import lombok.Data;

/**
 * @author marshallwu
 * @date 2022/4/29 13:43
 */
@Data
public class SortingParam {
    @JsonValue
    SortingType type;
    SortingDirection direction;
    Integer limit;
    String propertyName;
}
