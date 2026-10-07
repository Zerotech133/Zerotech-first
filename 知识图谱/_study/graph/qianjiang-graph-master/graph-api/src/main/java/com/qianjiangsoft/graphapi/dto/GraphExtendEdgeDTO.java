package com.qianjiangsoft.graphapi.dto;

import lombok.Data;

/**
 * @author ulmarr8861
 * @date 2022/4/19
 */
@Data
public class GraphExtendEdgeDTO {
    private String id;
    private String name;
    private String sourceId;
    private String destinationId;
    private String edgeTypeName;

}
