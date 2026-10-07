package com.qianjiangsoft.graphapi.param;

import com.qianjiangsoft.graphcore.constants.ExtendDirection;
import com.qianjiangsoft.graphcore.model.PropertyInfo;
import com.qianjiangsoft.graphcore.param.Condition;
import com.qianjiangsoft.graphcore.param.SortingParam;
import lombok.Data;

import java.util.List;

/**
 * @author huangsanbao
 * @date 2022/4/8
 */
@Data
public class FlowDirectionParam {
    String edgeTypeName;
    Condition strengthCondition;

    private String taskName;
}
