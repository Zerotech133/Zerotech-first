package com.qianjiangsoft.graphcore.service;

import com.qianjiangsoft.graphcore.model.EdgeType;
import com.qianjiangsoft.graphcore.model.PropertyInfo;
import com.qianjiangsoft.graphcore.model.Tag;
import com.qianjiangsoft.graphcore.param.UpdateEdgeTypeParam;

import java.util.List;

/**
 * @author marshallwu
 * @date 2022/2/24 10:12
 */
public interface SchemaService {

    Tag createTag(String name, List<PropertyInfo> propertyInfoList, String iconPath);

    Tag createTag(String spaceId, String name, List<PropertyInfo> propertyInfoList, String iconPath);

    EdgeType createEdgeType(String name, List<PropertyInfo> propertyInfoList);

    EdgeType createEdgeType(String spaceId, String name, List<PropertyInfo> propertyInfoList);

    List<Tag> selectTagListBySpaceId(String spaceId);

    List<EdgeType> selectEdgeTypeListBySpaceId(String spaceId);

    Tag findTagByName(String name, String spaceId);

    EdgeType findEdgeTypeByName(String name, String spaceId);

    void deleteTagBySpaceId(String spaceId);

    void deleteEdgeTpeBySpaceId(String spaceId);

    void updateEdgeTypeById(UpdateEdgeTypeParam param);

    void updateTagById(String id, String name, List<PropertyInfo> propertyInfoList, String iconPath);

    void deleteEdgeTypeById(String id);

    Tag selectTagById(String id);

    void deleteTagById(String id);
}
