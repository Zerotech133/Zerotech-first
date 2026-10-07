package com.qianjiangsoft.graphapi.param;

import lombok.Data;

import java.util.List;

/**
 * @author marshallwu
 * @date 2022/2/23 16:34
 */
@Data
public class BatchCreateEdgeParam {

    List<CreateEdgeParam> edgeParamList;

    String edgeTypeName;
}
