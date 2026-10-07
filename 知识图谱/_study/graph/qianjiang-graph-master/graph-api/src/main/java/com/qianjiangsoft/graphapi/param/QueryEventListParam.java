package com.qianjiangsoft.graphapi.param;

import com.qianjiangsoft.graphcore.param.Condition;
import lombok.Data;

import java.util.List;

/**
 * @author ulmarr8861
 * @date 2022/3/30
 */
@Data
public class QueryEventListParam {
    private String spaceId;
    private List<String> typeList;

    Condition timeCondition;
}
