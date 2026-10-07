package com.qianjiangsoft.graphapi.param;

import lombok.Data;

/**
 * @author ulmarr8861
 * @date 2022/6/27
 */
@Data
public class MergeEdgeConvertParam {

    private String sourceId;

    private String destinationId;

    private String edgeTypeName;
}
