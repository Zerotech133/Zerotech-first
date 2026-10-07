package com.qianjiangsoft.graphapi.service;

import com.qianjiangsoft.graphcore.model.Tag;
import com.qianjiangsoft.graphapi.param.CreateTagParam;
import com.qianjiangsoft.graphapi.param.UpdateTagParam;

import java.util.List;

/**
 * @author ulmarr8861
 * @date 2022/3/18
 */

public interface TagService {

    void create(CreateTagParam param);

    void delete(String id);

    void update(UpdateTagParam param);

    List<Tag> selectBySpaceId(String spaceId);

    void createBatchTags(List<CreateTagParam> tagList);

    void deleteAllTag();

}
